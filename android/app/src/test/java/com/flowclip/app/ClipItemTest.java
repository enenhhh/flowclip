package com.flowclip.app;

import org.json.JSONException;
import org.json.JSONObject;
import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

public final class ClipItemTest {
    @Test
    public void textRejectsNul() {
        assertThrows(IllegalArgumentException.class,
                () -> ClipItem.text("device", "before\0after"));
    }

    @Test
    public void imageRejectsNonAsciiAndControlMime() {
        assertThrows(IllegalArgumentException.class,
                () -> ClipItem.image("device", "image/pn\u0261", "x.png", new byte[0]));
        assertThrows(IllegalArgumentException.class,
                () -> ClipItem.image("device", "image/png\ntext/plain", "x.png", new byte[0]));
    }

    @Test
    public void validatesIncomingMimeShape() {
        ClipItem.validateMime("text/plain; charset=utf-8", "text");
        ClipItem.validateMime("image/svg+xml", "image");
        ClipItem.validateMime(
                "IMAGE/VND.MICROSOFT.ICON; profile=srgb; version=1", "image");

        String[] invalid = {
                "image/",
                "/png",
                "image//png",
                "image /png",
                "image/png; broken",
                "image/png; name=\"sample\"",
                "image/png; name=sample=extra"
        };
        for (String mime : invalid) {
            assertThrows(IllegalArgumentException.class,
                    () -> ClipItem.validateMime(mime, "image"));
        }
    }

    @Test
    public void filenameMustBeAStringWhenPresent() throws JSONException {
        JSONObject raw = ClipItem.image(
                "device", "image/png", "sample.png", new byte[]{1}).toJson();
        raw.put("filename", 123);
        assertThrows(JSONException.class, () -> ClipItem.fromJson(raw, 1024));
        raw.put("filename", JSONObject.NULL);
        assertThrows(JSONException.class, () -> ClipItem.fromJson(raw, 1024));

        raw.remove("filename");
        assertEquals("", ClipItem.fromJson(raw, 1024).filename);
    }

    @Test
    public void base64MustUseCanonicalPaddingAndPadBits() throws JSONException {
        JSONObject raw = ClipItem.text("device", "a").toJson();
        raw.put("data", "YQ");
        assertThrows(JSONException.class, () -> ClipItem.fromJson(raw, 1024));
        raw.put("data", "YR==");
        assertThrows(JSONException.class, () -> ClipItem.fromJson(raw, 1024));
    }
}
