package com.dmz.api.client;

/**
 * Result of a successful send: the request ID assigned by the server and
 * the message ID that was forwarded to the gateway.
 */
public record SendResult(String requestId, String messageId) { }
