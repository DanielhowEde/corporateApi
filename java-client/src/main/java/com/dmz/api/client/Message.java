package com.dmz.api.client;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;

/**
 * Convenience builder for the DMZ Message payload used in schema-mode sends.
 *
 * <p>Returns a {@code Map<String, Object>} ready to hand to
 * {@link DmzApiClient#sendSchema(String, Map)}. Field names match the
 * server's wire format (TestID, Area, Date, Status).
 */
public final class Message {

    private final Map<String, Object> data = new LinkedHashMap<>();

    private Message() { }

    public static Message create() {
        return new Message();
    }

    public Message id(String id) {
        data.put("ID", Objects.requireNonNull(id, "id"));
        return this;
    }

    /** Generate a fresh UUID v4 as the message ID. */
    public Message generateId() {
        return id(UUID.randomUUID().toString());
    }

    public Message project(String project) {
        data.put("Project", Objects.requireNonNull(project, "project"));
        return this;
    }

    public Message testId(String testId) {
        data.put("TestID", Objects.requireNonNull(testId, "testId"));
        return this;
    }

    public Message area(String area) {
        data.put("Area", Objects.requireNonNull(area, "area"));
        return this;
    }

    /** ISO 8601 datetime string, e.g. {@code 2026-04-22T12:00:00}. */
    public Message date(String isoDate) {
        data.put("Date", Objects.requireNonNull(isoDate, "isoDate"));
        return this;
    }

    public Message status(String status) {
        data.put("Status", Objects.requireNonNull(status, "status"));
        return this;
    }

    public Message data(Map<String, String> kv) {
        data.put("Data", new LinkedHashMap<>(Objects.requireNonNull(kv, "kv")));
        return this;
    }

    public Map<String, Object> toMap() {
        return new LinkedHashMap<>(data);
    }
}
