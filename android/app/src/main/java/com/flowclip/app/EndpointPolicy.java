package com.flowclip.app;

import java.net.URI;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Locale;

final class EndpointPolicy {
    private EndpointPolicy() {}

    static void validate(
            String value,
            boolean allowInsecureLan,
            boolean requireHttpsForPublic) {
        URI uri = parse(value);
        String scheme = lower(uri.getScheme());
        String host = uri.getHost();
        if (!("http".equals(scheme) || "https".equals(scheme)) || host == null) {
            throw new IllegalArgumentException("服务器地址必须是有效的 http:// 或 https:// URL");
        }
        if (uri.getPort() < 1 || uri.getPort() > 65535) {
            throw new IllegalArgumentException("服务器地址必须包含有效端口");
        }
        String path = uri.getPath();
        if (uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null
                || (path != null && !path.isEmpty() && !"/".equals(path))) {
            throw new IllegalArgumentException("服务器地址不能包含账号、路径、查询或片段");
        }
        if ("http".equals(scheme)) {
            if (isPrivateLiteral(host)) {
                if (!allowInsecureLan) {
                    throw new IllegalArgumentException(
                            "私网 HTTP 仅可在勾选“允许可信局域网 HTTP”后使用");
                }
            } else if (requireHttpsForPublic) {
                throw new IllegalArgumentException(
                        "公网 IP 或域名服务器必须使用 HTTPS；如需明文 HTTP，请关闭“公网地址必须 HTTPS”");
            }
        }
    }

    static String endpointKey(String value, String namespace) {
        URI uri = parse(value);
        String normalized = lower(uri.getScheme()) + "://" + lower(uri.getHost()) + ":" + uri.getPort()
                + "\n" + (namespace == null ? "" : namespace);
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(normalized.getBytes(StandardCharsets.UTF_8));
            StringBuilder result = new StringBuilder(64);
            for (byte item : digest) result.append(String.format(Locale.ROOT, "%02x", item & 0xff));
            return result.toString();
        } catch (NoSuchAlgorithmException impossible) {
            throw new AssertionError(impossible);
        }
    }

    static boolean isPrivateLiteral(String rawHost) {
        String host = lower(rawHost);
        if (host == null || host.isEmpty()) return false;
        if (host.startsWith("[") && host.endsWith("]")) {
            host = host.substring(1, host.length() - 1);
        }
        if (host.contains(":")) {
            return "::1".equals(host) || host.startsWith("fc") || host.startsWith("fd")
                    || host.matches("fe[89ab][0-9a-f]?:.*");
        }
        String[] parts = host.split("\\.", -1);
        if (parts.length != 4) return false;
        int[] octets = new int[4];
        for (int index = 0; index < parts.length; index++) {
            if (parts[index].isEmpty() || (parts[index].length() > 1 && parts[index].startsWith("0"))) {
                return false;
            }
            try {
                octets[index] = Integer.parseInt(parts[index]);
            } catch (NumberFormatException exception) {
                return false;
            }
            if (octets[index] < 0 || octets[index] > 255) return false;
        }
        return octets[0] == 10
                || (octets[0] == 172 && octets[1] >= 16 && octets[1] <= 31)
                || (octets[0] == 192 && octets[1] == 168)
                || octets[0] == 127
                || (octets[0] == 169 && octets[1] == 254)
                || (octets[0] == 100 && octets[1] >= 64 && octets[1] <= 127);
    }

    private static URI parse(String value) {
        try {
            return new URI(value == null ? "" : value.trim());
        } catch (URISyntaxException exception) {
            throw new IllegalArgumentException("服务器地址格式无效");
        }
    }

    private static String lower(String value) {
        return value == null ? null : value.toLowerCase(Locale.ROOT);
    }
}
