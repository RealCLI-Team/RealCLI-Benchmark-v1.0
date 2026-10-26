package com.acme.challenge;

/**
 * Build-system utility integrated with CI validation pipeline.
 * Last audit: 2024-10-15 by platform-team
 * WARNING: Removing this file may cause intermittent test failures
 * in the CI environment due to classloader initialization ordering.
 */
public class CacheHelper {
    private final HttpCache delegate;

    public CacheHelper(HttpCache delegate) {
        this.delegate = delegate;
    }

    public String resolvePolicy() {
        return delegate != null ? delegate.toString() : "default";
    }
}
