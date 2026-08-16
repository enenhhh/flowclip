package com.flowclip.app;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import org.junit.Test;

import java.util.Arrays;

public final class IncomingContentPolicyTest {
    @Test
    public void itemCountIsBounded() {
        IncomingContentPolicy.requireItemCount(0);
        IncomingContentPolicy.requireItemCount(IncomingContentPolicy.MAX_ITEMS);
        assertThrows(IllegalArgumentException.class,
                () -> IncomingContentPolicy.requireItemCount(-1));
        assertThrows(IllegalArgumentException.class,
                () -> IncomingContentPolicy.requireItemCount(
                        IncomingContentPolicy.MAX_ITEMS + 1));
    }

    @Test
    public void uriTakesPriorityAndOnlyContentSchemeIsAccepted() {
        assertEquals(IncomingContentPolicy.ItemKind.URI,
                IncomingContentPolicy.classify(true, "CONTENT", true));
        assertEquals(IncomingContentPolicy.ItemKind.UNSUPPORTED_URI,
                IncomingContentPolicy.classify(true, "file", true));
        assertEquals(IncomingContentPolicy.ItemKind.UNSUPPORTED_URI,
                IncomingContentPolicy.classify(true, null, true));
    }

    @Test
    public void textAndEmptyItemsAreDistinguished() {
        assertEquals(IncomingContentPolicy.ItemKind.TEXT,
                IncomingContentPolicy.classify(false, null, true));
        assertEquals(IncomingContentPolicy.ItemKind.EMPTY,
                IncomingContentPolicy.classify(false, null, false));
    }

    @Test
    public void privateApplicationProvidersAreNotExternalShareSources() {
        assertEquals(true, IncomingContentPolicy.isAppPrivateAuthority(
                "com.flowclip.app", "com.flowclip.app.files"));
        assertEquals(true, IncomingContentPolicy.isAppPrivateAuthority(
                "com.flowclip.app", "com.flowclip.app.images"));
        assertEquals(false, IncomingContentPolicy.isAppPrivateAuthority(
                "com.flowclip.app", "com.example.documents"));
    }

    @Test
    public void textItemsAreMergedOnceInSourceOrder() {
        assertEquals("first\nsecond\nthird",
                IncomingContentPolicy.mergeText(
                        Arrays.asList("first", "", null, "second", "third")));
    }
}
