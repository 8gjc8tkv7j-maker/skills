package dev.tvhome;

import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.drawable.Drawable;

/** One thing the home screen can show as a tile: an app, a TV input or a built-in action. */
final class Entry {
    static final int APP = 0;
    static final int INPUT = 1;
    static final int SETTINGS = 2;
    static final int HOME_SETTINGS = 3;

    /** Stable id stored in dock.txt: a package name, "input:<tv input id>", "settings" or "home-settings". */
    final String id;
    final int kind;
    final CharSequence label;
    final Intent intent;
    final Drawable banner;
    final Drawable icon;

    /** Rendered tile artwork, shared by the dock tile and the grid tile of this entry. */
    Bitmap art;

    Entry(String id, int kind, CharSequence label, Intent intent, Drawable banner, Drawable icon) {
        this.id = id;
        this.kind = kind;
        this.label = label == null ? id : label;
        this.intent = intent;
        this.banner = banner;
        this.icon = icon;
    }
}
