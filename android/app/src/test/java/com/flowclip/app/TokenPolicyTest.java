package com.flowclip.app;

import org.junit.Test;

import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

public final class TokenPolicyTest {
    @Test
    public void acceptsVisibleAsciiAtLengthBoundaries() {
        TokenPolicy.validate(repeat('!', 16));
        TokenPolicy.validate(repeat('~', 512));
        TokenPolicy.validate("Abcdefghijk!~123");
    }

    @Test
    public void rejectsLengthsOutsideBoundariesWithClearMessage() {
        IllegalArgumentException shortError = assertThrows(IllegalArgumentException.class,
                () -> TokenPolicy.validate(repeat('a', 15)));
        IllegalArgumentException longError = assertThrows(IllegalArgumentException.class,
                () -> TokenPolicy.validate(repeat('a', 513)));
        assertTrue(shortError.getMessage().contains("16 到 512"));
        assertTrue(longError.getMessage().contains("16 到 512"));
    }

    @Test
    public void rejectsSpacesChineseAndControlCharacters() {
        IllegalArgumentException spaceError = assertThrows(IllegalArgumentException.class,
                () -> TokenPolicy.validate("123456789012345 "));
        IllegalArgumentException chineseError = assertThrows(IllegalArgumentException.class,
                () -> TokenPolicy.validate("123456789012345中"));
        IllegalArgumentException controlError = assertThrows(IllegalArgumentException.class,
                () -> TokenPolicy.validate("123456789012345\n"));
        assertTrue(spaceError.getMessage().contains("空格"));
        assertTrue(chineseError.getMessage().contains("中文"));
        assertTrue(controlError.getMessage().contains("控制字符"));
    }

    private static String repeat(char value, int count) {
        StringBuilder result = new StringBuilder(count);
        for (int index = 0; index < count; index++) result.append(value);
        return result.toString();
    }
}
