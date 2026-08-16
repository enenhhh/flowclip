package com.flowclip.app;

import org.junit.Test;
import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

public final class FileMetadataTest {
    private static final String ID = "123e4567-e89b-12d3-a456-426614174000";

    @Test
    public void filenameUsesNfcAndPortableLimits() {
        assertEquals("\u00e9.txt", FileMetadata.sanitizeFilename("e\u0301.txt"));
        String value = "\u6587".repeat(100);
        String normalized = FileMetadata.sanitizeFilename(value.substring(0, 60));
        assertEquals(180, normalized.getBytes(StandardCharsets.UTF_8).length);
        assertThrows(IllegalArgumentException.class,
                () -> FileMetadata.sanitizeFilename(value.substring(0, 61)));
    }

    @Test
    public void filenameRejectsPortableFilesystemHazards() {
        assertThrows(IllegalArgumentException.class,
                () -> FileMetadata.sanitizeFilename("../unsafe.txt"));
        assertThrows(IllegalArgumentException.class,
                () -> FileMetadata.sanitizeFilename("report?.txt"));
        assertThrows(IllegalArgumentException.class,
                () -> FileMetadata.sanitizeFilename("name. "));
        assertThrows(IllegalArgumentException.class,
                () -> FileMetadata.sanitizeFilename("CON.txt"));
        assertThrows(IllegalArgumentException.class,
                () -> FileMetadata.sanitizeFilename("COM1"));
        assertThrows(IllegalArgumentException.class,
                () -> FileMetadata.sanitizeFilename("LPT\u00b9.log"));
        assertThrows(IllegalArgumentException.class,
                () -> FileMetadata.sanitizeFilename("\ud800.txt"));
    }

    @Test
    public void mimeMatchesDesktopAsciiTypeSubtypeContract() {
        assertEquals("text/plain", FileMetadata.validateMime("Text/Plain"));
        assertEquals("application/vnd.example+json",
                FileMetadata.validateMime("application/vnd.example+json"));
        assertThrows(IllegalArgumentException.class,
                () -> FileMetadata.validateMime("text/plain; charset=UTF-8"));
        assertThrows(IllegalArgumentException.class,
                () -> FileMetadata.validateMime(" text/plain"));
        assertThrows(IllegalArgumentException.class,
                () -> FileMetadata.validateMime("text/%plain"));
        assertThrows(IllegalArgumentException.class,
                () -> FileMetadata.validateMime(".text/plain"));
    }

    @Test
    public void completedRecordRequiresReadyStatusAndChecksum() {
        FileMetadata pending = new FileMetadata(
                ID, "device", "file.bin", "application/octet-stream",
                12L, 1L, "pending", null);
        FileMetadata completed = pending.completed("a".repeat(64));
        assertEquals("pending", pending.status);
        assertNull(pending.sha256);
        assertEquals("ready", completed.status);
        assertEquals("a".repeat(64), completed.sha256);
        assertTrue(pending.sameUpload(completed));
        assertThrows(IllegalArgumentException.class, () -> new FileMetadata(
                ID, "device", "file.bin", "application/octet-stream",
                12L, 1L, "ready", null));
    }

    @Test
    public void filesV1JsonUsesStatusInsteadOfPerItemRevision() throws Exception {
        FileMetadata pending = new FileMetadata(
                ID, "device", "\u4e2d\u6587\u6587\u4ef6.zip", "application/zip",
                42L, 123L, "pending", null);
        JSONObject upload = pending.toUploadJson();
        assertFalse(upload.has("status"));
        assertFalse(upload.has("sha256"));
        assertFalse(upload.has("revision"));
        assertTrue(pending.sameUpload(FileMetadata.fromUploadJson(upload, 42L)));

        JSONObject pendingJson = pending.toJson();
        assertEquals("pending", pendingJson.getString("status"));
        assertFalse(pendingJson.has("sha256"));
        FileMetadata completed = pending.completed("b".repeat(64));
        FileMetadata decoded = FileMetadata.fromJson(completed.toJson(), 42L);
        assertEquals("ready", decoded.status);
        assertEquals("b".repeat(64), decoded.sha256);
        assertFalse(completed.toJson().has("revision"));
    }

    @Test
    public void jsonIntegerFieldsRejectFractionsAndOverflow() throws Exception {
        JSONObject upload = new JSONObject()
                .put("id", ID)
                .put("origin", "device")
                .put("filename", "file.bin")
                .put("mime", "application/octet-stream")
                .put("size", 1.5d)
                .put("createdAt", 1L);
        assertThrows(JSONException.class,
                () -> FileMetadata.fromUploadJson(upload, Long.MAX_VALUE));
        upload.put("size", new java.math.BigInteger("9223372036854775808"));
        assertThrows(JSONException.class,
                () -> FileMetadata.fromUploadJson(upload, Long.MAX_VALUE));
    }

    @Test
    public void identifiersMustBeCanonicalUuidAndOriginMustBeValidUnicode() {
        assertThrows(IllegalArgumentException.class, () -> new FileMetadata(
                "safe-id", "device", "file.bin", "application/octet-stream",
                0L, 1L, "pending", null));
        assertThrows(IllegalArgumentException.class, () -> new FileMetadata(
                ID.toUpperCase(), "device", "file.bin", "application/octet-stream",
                0L, 1L, "pending", null));
        assertThrows(IllegalArgumentException.class, () -> new FileMetadata(
                ID, "\ud800", "file.bin", "application/octet-stream",
                0L, 1L, "pending", null));
    }
}
