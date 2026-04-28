package com.dmz.api.client;

/**
 * Thrown for any error reaching or talking to the DMZ API.
 *
 * <p>{@link #statusCode()} is set when the failure was an HTTP non-2xx
 * response; {@code -1} otherwise (network errors, TLS setup, JSON parse).
 */
public class DmzApiException extends RuntimeException {

    private final int statusCode;

    public DmzApiException(String message) {
        super(message);
        this.statusCode = -1;
    }

    public DmzApiException(String message, Throwable cause) {
        super(message, cause);
        this.statusCode = -1;
    }

    public DmzApiException(String message, int statusCode) {
        super(message);
        this.statusCode = statusCode;
    }

    /** HTTP status code if available, or {@code -1} for transport errors. */
    public int statusCode() {
        return statusCode;
    }
}
