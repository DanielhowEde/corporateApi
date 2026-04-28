package com.dmz.api.client;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManagerFactory;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * Client for the DMZ API programmatic surface (/api/v1).
 *
 * <p>Two send modes are supported, mirroring the server:
 * <ul>
 *   <li>Template mode — name a project + template; the server loads the
 *       template from disk, regenerates ID + Date, validates and forwards.</li>
 *   <li>Schema mode — supply the full payload; the server validates it
 *       against every JSON Schema configured for the project (all must pass)
 *       and forwards.</li>
 * </ul>
 *
 * <p>Authentication is mutual TLS: construct the client with the PFX bundle
 * issued by the corporate admin panel ({@code /admin/keys}). The cert's
 * Common Name must be bound to the project the caller targets.
 */
public final class DmzApiClient implements AutoCloseable {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private final URI baseUri;
    private final HttpClient http;
    private final Duration timeout;

    private DmzApiClient(URI baseUri, HttpClient http, Duration timeout) {
        this.baseUri = baseUri;
        this.http = http;
        this.timeout = timeout;
    }

    // -----------------------------------------------------------------
    // Builder
    // -----------------------------------------------------------------

    public static Builder builder() {
        return new Builder();
    }

    public static final class Builder {
        private URI baseUri;
        private Path pfxPath;
        private char[] pfxPassword;
        private Path caPemPath;
        private Duration timeout = Duration.ofSeconds(30);

        /** API base URI, e.g. {@code https://corporate-api.example.com}. */
        public Builder baseUri(String uri) {
            this.baseUri = URI.create(Objects.requireNonNull(uri, "baseUri"));
            return this;
        }

        /** Path to the PFX bundle issued by the corporate admin panel. */
        public Builder clientPfx(Path pfx, String password) {
            this.pfxPath = Objects.requireNonNull(pfx, "pfx");
            this.pfxPassword = Objects.requireNonNull(password, "password").toCharArray();
            return this;
        }

        /** Optional: PEM-encoded CA cert to add to the trust store. */
        public Builder trustCaPem(Path caPem) {
            this.caPemPath = caPem;
            return this;
        }

        public Builder timeout(Duration timeout) {
            this.timeout = Objects.requireNonNull(timeout, "timeout");
            return this;
        }

        public DmzApiClient build() {
            if (baseUri == null) {
                throw new IllegalStateException("baseUri is required");
            }
            try {
                SSLContext ctx = buildSslContext(pfxPath, pfxPassword, caPemPath);
                HttpClient http = HttpClient.newBuilder()
                        .sslContext(ctx)
                        .connectTimeout(timeout)
                        .version(HttpClient.Version.HTTP_1_1)
                        .build();
                return new DmzApiClient(baseUri, http, timeout);
            } catch (Exception e) {
                throw new DmzApiException("Failed to initialise TLS context: " + e.getMessage(), e);
            }
        }
    }

    // -----------------------------------------------------------------
    // Public API
    // -----------------------------------------------------------------

    /**
     * Template mode — server loads {@code {project}/{templateName}.json},
     * regenerates ID + Date, validates and forwards.
     *
     * @param project       3-character project code
     * @param templateName  template filename without extension
     * @param overrides     optional shallow-merge payload (may be null)
     * @param verbatim      when true, server skips ID/Date regeneration
     */
    public SendResult sendTemplate(String project, String templateName,
                                   Map<String, Object> overrides, boolean verbatim) {
        Objects.requireNonNull(project, "project");
        Objects.requireNonNull(templateName, "templateName");
        Map<String, Object> body = Map.of(
                "overrides", overrides == null ? Map.of() : overrides,
                "verbatim", verbatim
        );
        return postForResult(
                "/api/v1/messages/template/" + enc(project) + "/" + enc(templateName),
                body
        );
    }

    /**
     * Schema mode — caller supplies the full Message payload. The server
     * validates it against every project schema (all must pass) and forwards.
     */
    public SendResult sendSchema(String project, Map<String, Object> payload) {
        Objects.requireNonNull(project, "project");
        Objects.requireNonNull(payload, "payload");
        return postForResult("/api/v1/messages/schema/" + enc(project), payload);
    }

    /** List template names (without .json) available for the project. */
    public List<String> listTemplates(String project) {
        Objects.requireNonNull(project, "project");
        JsonNode body = getJson("/api/v1/payloads/" + enc(project));
        return readStringList(body, "templates");
    }

    /** List JSON Schema names (without .json) configured for the project. */
    public List<String> listSchemas(String project) {
        Objects.requireNonNull(project, "project");
        JsonNode body = getJson("/api/v1/payloads/" + enc(project) + "/schemas");
        return readStringList(body, "schemas");
    }

    @Override
    public void close() {
        // HttpClient has no close() in JDK 17; nothing to release.
    }

    // -----------------------------------------------------------------
    // Internals
    // -----------------------------------------------------------------

    private SendResult postForResult(String path, Object body) {
        HttpResponse<String> response = post(path, body);
        JsonNode json = parseJson(response.body());
        if (response.statusCode() / 100 != 2) {
            throw new DmzApiException(
                    "API call failed: status=" + response.statusCode()
                            + " requestId=" + textOrNull(json, "request_id")
                            + " error=" + textOrNull(json, "error"),
                    response.statusCode());
        }
        return new SendResult(
                textOrNull(json, "request_id"),
                textOrNull(json, "message_id")
        );
    }

    private JsonNode getJson(String path) {
        HttpResponse<String> response = send(HttpRequest.newBuilder()
                .uri(baseUri.resolve(path))
                .timeout(timeout)
                .GET()
                .header("Accept", "application/json"));
        if (response.statusCode() / 100 != 2) {
            throw new DmzApiException(
                    "GET " + path + " failed: status=" + response.statusCode(),
                    response.statusCode());
        }
        return parseJson(response.body());
    }

    private HttpResponse<String> post(String path, Object body) {
        byte[] payload;
        try {
            payload = MAPPER.writeValueAsBytes(body);
        } catch (IOException e) {
            throw new DmzApiException("Failed to serialise request body: " + e.getMessage(), e);
        }
        return send(HttpRequest.newBuilder()
                .uri(baseUri.resolve(path))
                .timeout(timeout)
                .POST(HttpRequest.BodyPublishers.ofByteArray(payload))
                .header("Content-Type", "application/json")
                .header("Accept", "application/json"));
    }

    private HttpResponse<String> send(HttpRequest.Builder req) {
        try {
            return http.send(req.build(), HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            throw new DmzApiException("Network error: " + e.getMessage(), e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new DmzApiException("Interrupted while waiting for response", e);
        }
    }

    private static SSLContext buildSslContext(Path pfx, char[] pwd, Path caPem) throws Exception {
        KeyManagerFactory kmf = null;
        if (pfx != null) {
            KeyStore ks = KeyStore.getInstance("PKCS12");
            try (var in = Files.newInputStream(pfx)) {
                ks.load(in, pwd);
            }
            kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
            kmf.init(ks, pwd);
        }

        TrustManagerFactory tmf = TrustManagerFactory.getInstance(
                TrustManagerFactory.getDefaultAlgorithm());
        if (caPem != null) {
            KeyStore trust = KeyStore.getInstance(KeyStore.getDefaultType());
            trust.load(null, null);
            try (var in = Files.newInputStream(caPem)) {
                var cf = java.security.cert.CertificateFactory.getInstance("X.509");
                int idx = 0;
                for (var cert : cf.generateCertificates(in)) {
                    trust.setCertificateEntry("dmz-ca-" + idx++, cert);
                }
            }
            tmf.init(trust);
        } else {
            tmf.init((KeyStore) null);
        }

        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(kmf == null ? null : kmf.getKeyManagers(), tmf.getTrustManagers(), null);
        return ctx;
    }

    private static JsonNode parseJson(String body) {
        if (body == null || body.isEmpty()) {
            return MAPPER.nullNode();
        }
        try {
            return MAPPER.readTree(body);
        } catch (IOException e) {
            throw new DmzApiException("Server returned non-JSON body: " + body, e);
        }
    }

    private static List<String> readStringList(JsonNode node, String field) {
        JsonNode arr = node.get(field);
        if (arr == null || !arr.isArray()) {
            return List.of();
        }
        return java.util.stream.StreamSupport.stream(arr.spliterator(), false)
                .map(JsonNode::asText)
                .toList();
    }

    private static String textOrNull(JsonNode node, String field) {
        JsonNode v = node.get(field);
        return v == null || v.isNull() ? null : v.asText();
    }

    private static String enc(String segment) {
        return java.net.URLEncoder.encode(segment, java.nio.charset.StandardCharsets.UTF_8)
                .replace("+", "%20");
    }
}
