package com.dmz.api.client;

import org.junit.jupiter.api.Test;

import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;

class MessageTest {

    @Test
    void buildsExpectedMap() {
        Map<String, Object> m = Message.create()
                .generateId()
                .project("AAA")
                .testId("TST001")
                .area("Integration")
                .date("2026-04-22T12:00:00")
                .status("Inprogress")
                .data(Map.of("k", "v"))
                .toMap();

        assertEquals("AAA", m.get("Project"));
        assertEquals("TST001", m.get("TestID"));
        assertEquals("Integration", m.get("Area"));
        assertEquals("2026-04-22T12:00:00", m.get("Date"));
        assertEquals("Inprogress", m.get("Status"));
        assertEquals(Map.of("k", "v"), m.get("Data"));

        assertDoesNotThrow(() -> UUID.fromString((String) m.get("ID")));
    }

    @Test
    void rejectsNullValues() {
        assertThrows(NullPointerException.class, () -> Message.create().project(null));
    }
}
