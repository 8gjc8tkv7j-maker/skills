package dev.tvhome;

import android.content.Context;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.Charset;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;

/**
 * Plain files in the app's external files dir, so the dock and settings can be read and written over
 * adb (/sdcard/Android/data/dev.tvhome/files) as well as from the TV itself.
 */
final class Store {
    private static final Charset UTF8 = Charset.forName("UTF-8");

    final File dir;
    private final File dockFile;
    private final File propsFile;
    private final Properties props = new Properties();
    private long dockStamp = -1;
    private long propsStamp = -1;

    Store(Context c) {
        File ext = null;
        try {
            ext = c.getExternalFilesDir(null);
        } catch (RuntimeException ignored) {
            // Fall back to internal storage below.
        }
        dir = ext != null ? ext : c.getFilesDir();
        dockFile = new File(dir, "dock.txt");
        propsFile = new File(dir, "home.properties");
        wallpaperDir().mkdirs();
        loadProps();
    }

    File wallpaperDir() {
        return new File(dir, "wallpapers");
    }

    boolean hasDock() {
        return dockFile.isFile();
    }

    boolean dockChanged() {
        return dockFile.lastModified() != dockStamp;
    }

    List<String> readDock() {
        ArrayList<String> out = new ArrayList<>();
        dockStamp = dockFile.lastModified();
        if (!dockFile.isFile()) {
            return out;
        }
        try (BufferedReader r = new BufferedReader(
                new InputStreamReader(new FileInputStream(dockFile), UTF8))) {
            String line;
            while ((line = r.readLine()) != null) {
                line = line.trim();
                if (!line.isEmpty() && !line.startsWith("#") && !out.contains(line)) {
                    out.add(line);
                }
            }
        } catch (IOException ignored) {
            // An unreadable dock is treated as empty.
        }
        return out;
    }

    void writeDock(List<String> ids) {
        StringBuilder sb = new StringBuilder();
        sb.append("# TV Home dock: one app package (or settings, input:<id>) per line, left to right.\n");
        for (String id : ids) {
            sb.append(id).append('\n');
        }
        writeAtomically(dockFile, sb.toString());
        dockStamp = dockFile.lastModified();
    }

    /** apps.tsv: what can go in the dock, with names, so tvctl.py can offer a readable list. */
    void writeApps(List<Entry> entries) {
        StringBuilder sb = new StringBuilder("# id\tlabel (written by TV Home for tvctl.py)\n");
        for (Entry e : entries) {
            sb.append(e.id).append('\t')
                    .append(e.label.toString().replace('\t', ' ').replace('\n', ' ')).append('\n');
        }
        writeAtomically(new File(dir, "apps.tsv"), sb.toString());
    }

    boolean propsChanged() {
        return propsFile.lastModified() != propsStamp;
    }

    void loadProps() {
        props.clear();
        propsStamp = propsFile.lastModified();
        if (!propsFile.isFile()) {
            return;
        }
        try (FileInputStream in = new FileInputStream(propsFile)) {
            props.load(in);
        } catch (IOException ignored) {
            // Defaults apply.
        }
    }

    int rotateMinutes() {
        try {
            int v = Integer.parseInt(props.getProperty("rotate_minutes", "3").trim());
            return Math.max(1, Math.min(1440, v));
        } catch (NumberFormatException e) {
            return 3;
        }
    }

    boolean onlinePhotos() {
        return bool("online_photos", true);
    }

    boolean motion() {
        return bool("motion", true);
    }

    boolean clock() {
        return bool("clock", true);
    }

    void put(String key, String value) {
        props.setProperty(key, value);
        StringBuilder sb = new StringBuilder("# TV Home settings\n");
        for (String k : props.stringPropertyNames()) {
            sb.append(k).append('=').append(props.getProperty(k)).append('\n');
        }
        writeAtomically(propsFile, sb.toString());
        propsStamp = propsFile.lastModified();
    }

    private boolean bool(String key, boolean def) {
        String v = props.getProperty(key);
        return v == null ? def : "true".equalsIgnoreCase(v.trim()) || "on".equalsIgnoreCase(v.trim());
    }

    private static void writeAtomically(File target, String text) {
        File tmp = new File(target.getPath() + ".tmp");
        try (Writer w = new OutputStreamWriter(new FileOutputStream(tmp), UTF8)) {
            w.write(text);
        } catch (IOException e) {
            tmp.delete();
            return;
        }
        if (!tmp.renameTo(target)) {
            tmp.delete();
        }
    }
}
