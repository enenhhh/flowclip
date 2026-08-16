package com.flowclip.app;

import java.util.List;

final class IncomingContentPolicy {
    static final int MAX_ITEMS = 16;

    enum ItemKind {
        URI,
        TEXT,
        EMPTY,
        UNSUPPORTED_URI
    }

    private IncomingContentPolicy() {}

    static void requireItemCount(int count) {
        if (count < 0 || count > MAX_ITEMS) {
            throw new IllegalArgumentException("一次最多发送 " + MAX_ITEMS + " 项内容");
        }
    }

    static ItemKind classify(boolean hasUri, String uriScheme, boolean hasText) {
        if (hasUri) {
            return "content".equalsIgnoreCase(uriScheme)
                    ? ItemKind.URI : ItemKind.UNSUPPORTED_URI;
        }
        return hasText ? ItemKind.TEXT : ItemKind.EMPTY;
    }

    static boolean isAppPrivateAuthority(String packageName, String authority) {
        if (packageName == null || authority == null) return false;
        return (packageName + ".files").equals(authority)
                || (packageName + ".images").equals(authority);
    }

    static String mergeText(List<String> values) {
        StringBuilder merged = new StringBuilder();
        for (String value : values) {
            if (value == null || value.isEmpty()) continue;
            if (merged.length() > 0) merged.append('\n');
            merged.append(value);
        }
        return merged.toString();
    }
}
