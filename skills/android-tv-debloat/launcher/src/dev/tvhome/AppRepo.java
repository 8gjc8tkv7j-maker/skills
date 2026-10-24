package dev.tvhome;

import android.content.Context;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.drawable.Drawable;
import android.media.tv.TvContract;
import android.media.tv.TvInputInfo;
import android.media.tv.TvInputManager;
import android.os.Build;
import android.provider.Settings;

import java.text.Collator;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Set;

/** Finds what can go on the home screen: launchable apps, HDMI inputs and system shortcuts. */
final class AppRepo {
    static final String SETTINGS_ID = "settings";
    static final String HOME_SETTINGS_ID = "home-settings";
    static final String INPUT_PREFIX = "input:";

    /** Apps people usually want in the dock, in the order they are picked for a fresh dock. */
    static final String[] POPULAR = {
        "com.netflix.ninja",
        "com.google.android.youtube.tv",
        "com.amazon.amazonvideo.livingroom",
        "com.disney.disneyplus",
        "com.wbd.stream",
        "com.apple.atve.androidtv.appletv",
        "com.plexapp.android",
        "org.jellyfin.androidtv",
        "org.xbmc.kodi",
        "com.spotify.tv.android",
        "tv.twitch.android.app",
        "com.google.android.youtube.tvmusic",
    };

    static final class Catalog {
        static final Catalog EMPTY =
                new Catalog(new ArrayList<Entry>(), new ArrayList<Entry>(), new ArrayList<Entry>());

        final List<Entry> apps;
        final List<Entry> inputs;
        final List<Entry> system;

        Catalog(List<Entry> apps, List<Entry> inputs, List<Entry> system) {
            this.apps = apps;
            this.inputs = inputs;
            this.system = system;
        }

        List<Entry> all() {
            ArrayList<Entry> out = new ArrayList<>(apps);
            out.addAll(inputs);
            out.addAll(system);
            return out;
        }
    }

    private AppRepo() {}

    static Catalog load(Context c) {
        PackageManager pm = c.getPackageManager();
        String self = c.getPackageName();
        List<Entry> system = system(c, pm);
        Set<String> skip = new HashSet<>();
        skip.add(self);
        for (Entry e : system) {
            if (e.intent != null && e.intent.getComponent() != null) {
                skip.add(e.intent.getComponent().getPackageName());
            }
        }

        LinkedHashMap<String, Entry> apps = new LinkedHashMap<>();
        addLaunchables(pm, apps, skip, Intent.CATEGORY_LEANBACK_LAUNCHER, true);
        addLaunchables(pm, apps, skip, Intent.CATEGORY_LAUNCHER, false);
        ArrayList<Entry> list = new ArrayList<>(apps.values());
        final Collator collator = Collator.getInstance();
        Collections.sort(list, new Comparator<Entry>() {
            @Override
            public int compare(Entry a, Entry b) {
                return collator.compare(a.label.toString(), b.label.toString());
            }
        });
        return new Catalog(list, inputs(c), system);
    }

    private static void addLaunchables(PackageManager pm, LinkedHashMap<String, Entry> out,
            Set<String> skip, String category, boolean leanback) {
        List<ResolveInfo> found;
        try {
            found = pm.queryIntentActivities(new Intent(Intent.ACTION_MAIN).addCategory(category), 0);
        } catch (RuntimeException e) {
            return;
        }
        for (ResolveInfo ri : found) {
            ActivityInfo ai = ri.activityInfo;
            if (ai == null || skip.contains(ai.packageName) || out.containsKey(ai.packageName)) {
                continue;
            }
            Drawable banner = null;
            if (leanback) {
                banner = ai.loadBanner(pm);
                if (banner == null && ai.applicationInfo != null) {
                    banner = ai.applicationInfo.loadBanner(pm);
                }
            }
            Drawable icon = banner == null ? ri.loadIcon(pm) : null;
            Intent launch = new Intent(Intent.ACTION_MAIN)
                    .addCategory(category)
                    .setClassName(ai.packageName, ai.name)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
            out.put(ai.packageName,
                    new Entry(ai.packageName, Entry.APP, ri.loadLabel(pm), launch, banner, icon));
        }
    }

    /** HDMI and other passthrough inputs on TVs (not on streaming boxes). */
    static List<Entry> inputs(Context c) {
        ArrayList<Entry> out = new ArrayList<>();
        if (!c.getPackageManager().hasSystemFeature(PackageManager.FEATURE_LIVE_TV)) {
            return out;
        }
        try {
            TvInputManager tim = (TvInputManager) c.getSystemService(Context.TV_INPUT_SERVICE);
            if (tim == null) {
                return out;
            }
            for (TvInputInfo info : tim.getTvInputList()) {
                if (!info.isPassthroughInput() || info.getParentId() != null) {
                    continue;
                }
                if (Build.VERSION.SDK_INT >= 23 && info.isHidden(c)) {
                    continue;
                }
                CharSequence label = null;
                if (Build.VERSION.SDK_INT >= 24) {
                    label = info.loadCustomLabel(c);
                }
                if (label == null || label.length() == 0) {
                    label = info.loadLabel(c);
                }
                Intent view = new Intent(Intent.ACTION_VIEW,
                        TvContract.buildChannelUriForPassthroughInput(info.getId()))
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                out.add(new Entry(INPUT_PREFIX + info.getId(), Entry.INPUT, label, view, null, null));
            }
        } catch (RuntimeException ignored) {
            // Some TVs restrict the input list; the home screen simply shows no inputs then.
        }
        return out;
    }

    private static List<Entry> system(Context c, PackageManager pm) {
        ArrayList<Entry> out = new ArrayList<>();
        Intent settings = new Intent(Settings.ACTION_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        ResolveInfo ri = pm.resolveActivity(settings, 0);
        if (ri != null && ri.activityInfo != null) {
            settings.setClassName(ri.activityInfo.packageName, ri.activityInfo.name);
            out.add(new Entry(SETTINGS_ID, Entry.SETTINGS, c.getString(R.string.settings), settings,
                    null, ri.loadIcon(pm)));
        }
        out.add(new Entry(HOME_SETTINGS_ID, Entry.HOME_SETTINGS, c.getString(R.string.home_settings),
                null, null, c.getDrawable(R.drawable.icon)));
        return out;
    }

    /** A first dock: popular streaming apps that are installed, then Settings. */
    static List<String> defaultDock(Catalog c) {
        Set<String> installed = new HashSet<>();
        for (Entry e : c.apps) {
            installed.add(e.id);
        }
        ArrayList<String> out = new ArrayList<>();
        for (String pkg : POPULAR) {
            if (installed.contains(pkg) && out.size() < 6) {
                out.add(pkg);
            }
        }
        for (Entry e : c.apps) {
            if (out.size() >= 4) {
                break;
            }
            if (!out.contains(e.id)) {
                out.add(e.id);
            }
        }
        for (Entry e : c.system) {
            if (e.kind == Entry.SETTINGS) {
                out.add(e.id);
            }
        }
        return out;
    }
}
