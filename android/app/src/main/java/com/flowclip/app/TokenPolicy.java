package com.flowclip.app;

final class TokenPolicy {
    static final int MIN_LENGTH = 16;
    static final int MAX_LENGTH = 512;

    private TokenPolicy() {}

    static void validate(String token) {
        if (token == null || token.isEmpty()) {
            throw new IllegalArgumentException("共享密钥不能为空");
        }
        if (token.length() < MIN_LENGTH || token.length() > MAX_LENGTH) {
            throw new IllegalArgumentException("共享密钥长度应为 16 到 512 个字符（当前 "
                    + token.length() + " 个）");
        }
        for (int index = 0; index < token.length(); index++) {
            char value = token.charAt(index);
            if (value == ' ') {
                throw new IllegalArgumentException("共享密钥第 " + (index + 1)
                        + " 个字符是空格；仅允许可见 ASCII 字符（! 到 ~）");
            }
            if (value < 0x21 || value > 0x7e) {
                throw new IllegalArgumentException("共享密钥第 " + (index + 1)
                        + " 个字符无效；不能包含中文、控制字符或其他非 ASCII 字符");
            }
        }
    }
}
