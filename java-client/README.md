# DMZ API Java Client

Java 17+ client library for the DMZ API programmatic surface (`/api/v1`).

## Build

```
mvn package
```

Produces `target/dmz-api-client-1.0.0.jar` plus a sources jar.

## Linking into your app

Either install to your local repo:

```
mvn install
```

then depend on it:

```xml
<dependency>
    <groupId>com.dmz.api</groupId>
    <artifactId>dmz-api-client</artifactId>
    <version>1.0.0</version>
</dependency>
```

…or drop the jar onto your classpath directly. Transitive deps are
`jackson-databind` and `slf4j-api`; HTTP and TLS use the JDK built-ins.

## Authentication

Issue a client cert via the corporate admin panel (`/admin/keys`),
choosing the project that this client is allowed to send for. Download
the `.pfx` bundle (default password `changeme`).

## Usage

```java
import com.dmz.api.client.DmzApiClient;
import com.dmz.api.client.Message;
import com.dmz.api.client.SendResult;

import java.nio.file.Path;
import java.util.Map;

try (DmzApiClient client = DmzApiClient.builder()
        .baseUri("https://corporate-api.example.com")
        .clientPfx(Path.of("alice.pfx"), "changeme")
        .trustCaPem(Path.of("ca.crt"))   // optional
        .build()) {

    // Template mode — server loads + regenerates ID/Date
    SendResult r1 = client.sendTemplate("AAA", "smoke", null, false);
    System.out.println("sent " + r1.messageId());

    // Schema mode — full payload validated against project schemas
    SendResult r2 = client.sendSchema("AAA", Message.create()
            .generateId()
            .project("AAA")
            .testId("TST001")
            .area("Integration")
            .date("2026-04-22T12:00:00")
            .status("Inprogress")
            .data(Map.of("result", "pass"))
            .toMap());

    // Discovery
    System.out.println(client.listTemplates("AAA"));
    System.out.println(client.listSchemas("AAA"));
}
```

## Errors

All failures throw `DmzApiException`. `statusCode()` is the HTTP status
when the failure was a non-2xx response, or `-1` for transport / TLS /
JSON-parse errors.
