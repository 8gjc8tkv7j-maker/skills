package dev.tvhome;

import android.content.Context;
import android.content.SharedPreferences;
import android.text.Html;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.Charset;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Random;

/**
 * Downloads featured nature photos from Wikimedia Commons (free licences; the credit line is shown
 * on screen) into a small cache. Runs at most every few hours and silently gives up when offline.
 */
final class PhotoFetcher {
    private static final String API = "https://commons.wikimedia.org/w/api.php";
    private static final String ROOT = "Category:Featured pictures of landscapes";
    private static final String UA =
            "TVHome/1.0 (Android TV home screen; https://github.com/8gjc8tkv7j-maker/skills)";
    private static final String[] NATURE = {
        "landscape", "mountain", "lake", "river", "waterfall", "forest", "coast", "beach", "desert",
        "sea", "ocean", "valley", "glacier", "canyon", "island", "volcano", "sky", "sunset",
        "sunrise", "field", "meadow", "nature", "snow", "cliff", "fjord", "hill", "rock",
    };
    private static final String[] NOT_NATURE = {"city", "cityscape", "urban", "building", "skyline"};
    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final int KEEP = 40;
    private static final int PER_ROUND = 6;
    private static final long EVERY_MS = 6 * 3600_000L;
    private static final long CATEGORIES_EVERY_MS = 7 * 24 * 3600_000L;
    private static final int MAX_BYTES = 12 * 1024 * 1024;

    private PhotoFetcher() {}

    static File dir(Context c) {
        File d = new File(c.getFilesDir(), "photos");
        d.mkdirs();
        return d;
    }

    static List<Wallpapers.Pic> cached(Context c) {
        ArrayList<Wallpapers.Pic> out = new ArrayList<>();
        File[] files = dir(c).listFiles();
        if (files == null) {
            return out;
        }
        for (File f : files) {
            if (!f.getName().endsWith(".jpg")) {
                continue;
            }
            File meta = new File(f.getPath().replaceFirst("\\.jpg$", ".txt"));
            out.add(new Wallpapers.Pic(f.getPath(), null, f, readText(meta).trim()));
        }
        return out;
    }

    static void maybeFetch(Context c) {
        SharedPreferences prefs = c.getSharedPreferences("photos", Context.MODE_PRIVATE);
        long last = prefs.getLong("fetched_at", 0);
        int have = cached(c).size();
        long now = System.currentTimeMillis();
        if (have >= 12 && now - last < EVERY_MS && now >= last) {
            return;
        }
        try {
            fetch(c, prefs);
            prefs.edit().putLong("fetched_at", now).apply();
        } catch (IOException | JSONException | RuntimeException ignored) {
            // Offline, blocked or the API changed: keep using what is cached.
        }
    }

    private static void fetch(Context c, SharedPreferences prefs) throws IOException, JSONException {
        List<String> cats = categories(prefs);
        Random rnd = new Random();
        String cat = cats.get(rnd.nextInt(cats.size()));
        String start = String.valueOf((char) ('A' + rnd.nextInt(26)));
        JSONArray pages = filePages(cat, start);
        if (pages == null || pages.length() < 4) {
            pages = filePages(cat, null);
        }
        if (pages == null) {
            return;
        }
        ArrayList<JSONObject> list = new ArrayList<>();
        for (int i = 0; i < pages.length(); i++) {
            list.add(pages.getJSONObject(i));
        }
        Collections.shuffle(list, rnd);
        File dir = dir(c);
        int got = 0;
        for (JSONObject page : list) {
            if (got >= PER_ROUND) {
                break;
            }
            JSONArray infos = page.optJSONArray("imageinfo");
            if (infos == null || infos.length() == 0) {
                continue;
            }
            JSONObject info = infos.getJSONObject(0);
            int w = info.optInt("width");
            int h = info.optInt("height");
            if (!"image/jpeg".equals(info.optString("mime")) || w < 1600 || w < h * 1.3) {
                continue;
            }
            String url = info.optString("thumburl", info.optString("url"));
            if (!url.startsWith("https://upload.wikimedia.org/")) {
                continue;
            }
            String title = page.optString("title");
            String key = sha1(title);
            File jpg = new File(dir, key + ".jpg");
            if (jpg.exists()) {
                continue;
            }
            File tmp = new File(dir, key + ".part");
            if (!download(url, tmp) || !tmp.renameTo(jpg)) {
                tmp.delete();
                continue;
            }
            writeText(new File(dir, key + ".txt"), caption(title, info.optJSONObject("extmetadata")));
            got++;
        }
        trim(dir);
    }

    private static JSONArray filePages(String cat, String startPrefix) throws IOException, JSONException {
        String url = API + "?action=query&format=json&formatversion=2"
                + "&generator=categorymembers&gcmtype=file&gcmlimit=40&gcmsort=sortkey"
                + "&gcmtitle=" + enc(cat)
                + (startPrefix == null ? "" : "&gcmstartsortkeyprefix=" + enc(startPrefix))
                + "&prop=imageinfo&iiprop=url%7Csize%7Cmime%7Cextmetadata&iiurlwidth=1920"
                + "&iiextmetadatafilter=ObjectName%7CArtist%7CLicenseShortName";
        JSONObject query = new JSONObject(get(url)).optJSONObject("query");
        return query == null ? null : query.optJSONArray("pages");
    }

    /** The landscape category plus its nature-themed subcategories, refreshed weekly. */
    private static List<String> categories(SharedPreferences prefs) {
        ArrayList<String> out = new ArrayList<>();
        out.add(ROOT);
        String saved = prefs.getString("categories", "");
        long at = prefs.getLong("categories_at", 0);
        if (!saved.isEmpty() && System.currentTimeMillis() - at < CATEGORIES_EVERY_MS) {
            out.addAll(Arrays.asList(saved.split("\n")));
            return out;
        }
        try {
            String url = API + "?action=query&format=json&formatversion=2&list=categorymembers"
                    + "&cmtype=subcat&cmlimit=50&cmtitle=" + enc(ROOT);
            JSONObject query = new JSONObject(get(url)).optJSONObject("query");
            JSONArray members = query == null ? null : query.optJSONArray("categorymembers");
            StringBuilder sb = new StringBuilder();
            for (int i = 0; members != null && i < members.length(); i++) {
                String t = members.getJSONObject(i).optString("title");
                if (isNature(t)) {
                    out.add(t);
                    sb.append(sb.length() == 0 ? "" : "\n").append(t);
                }
            }
            prefs.edit().putString("categories", sb.toString())
                    .putLong("categories_at", System.currentTimeMillis()).apply();
        } catch (IOException | JSONException e) {
            // The root category alone is fine.
        }
        return out;
    }

    static boolean isNature(String title) {
        String t = title.toLowerCase(Locale.ROOT);
        for (String bad : NOT_NATURE) {
            if (t.contains(bad)) {
                return false;
            }
        }
        for (String good : NATURE) {
            if (t.contains(good)) {
                return true;
            }
        }
        return false;
    }

    /** "Moraine Lake — Photo: Jane Doe · CC BY-SA 4.0" */
    static String caption(String fileTitle, JSONObject meta) {
        String name = text(meta, "ObjectName");
        if (name.isEmpty()) {
            name = fileTitle.replaceFirst("^File:", "").replaceFirst("\\.[A-Za-z]+$", "")
                    .replace('_', ' ');
        }
        String artist = text(meta, "Artist");
        if (artist.length() > 48) {
            artist = artist.substring(0, 47) + "…";
        }
        String license = text(meta, "LicenseShortName");
        StringBuilder sb = new StringBuilder(name);
        if (!artist.isEmpty()) {
            sb.append(" — Photo: ").append(artist);
        }
        if (!license.isEmpty()) {
            sb.append(" · ").append(license);
        }
        return sb.toString();
    }

    @SuppressWarnings("deprecation")
    private static String text(JSONObject meta, String key) {
        if (meta == null || meta.optJSONObject(key) == null) {
            return "";
        }
        String html = meta.optJSONObject(key).optString("value", "");
        return Html.fromHtml(html).toString().replaceAll("\\s+", " ").trim();
    }

    private static String get(String url) throws IOException {
        HttpURLConnection conn = open(url);
        try (InputStream in = conn.getInputStream();
                BufferedReader r = new BufferedReader(new InputStreamReader(in, UTF8))) {
            StringBuilder sb = new StringBuilder();
            char[] buf = new char[8192];
            int n;
            while ((n = r.read(buf)) > 0) {
                sb.append(buf, 0, n);
                if (sb.length() > 4 * 1024 * 1024) {
                    throw new IOException("response too large");
                }
            }
            return sb.toString();
        } finally {
            conn.disconnect();
        }
    }

    private static boolean download(String url, File to) {
        HttpURLConnection conn = null;
        try {
            conn = open(url);
            if (conn.getResponseCode() != 200) {
                return false;
            }
            int total = 0;
            try (InputStream in = conn.getInputStream(); OutputStream out = new FileOutputStream(to)) {
                byte[] buf = new byte[16384];
                int n;
                while ((n = in.read(buf)) > 0) {
                    total += n;
                    if (total > MAX_BYTES) {
                        return false;
                    }
                    out.write(buf, 0, n);
                }
            }
            return total > 0;
        } catch (IOException e) {
            return false;
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    private static HttpURLConnection open(String url) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(30000);
        conn.setRequestProperty("User-Agent", UA);
        return conn;
    }

    private static void trim(File dir) {
        File[] jpgs = dir.listFiles();
        if (jpgs == null) {
            return;
        }
        ArrayList<File> list = new ArrayList<>();
        for (File f : jpgs) {
            if (f.getName().endsWith(".jpg")) {
                list.add(f);
            } else if (f.getName().endsWith(".part")) {
                f.delete();
            }
        }
        if (list.size() <= KEEP) {
            return;
        }
        Collections.sort(list, new Comparator<File>() {
            @Override
            public int compare(File a, File b) {
                return Long.compare(a.lastModified(), b.lastModified());
            }
        });
        for (int i = 0; i < list.size() - KEEP; i++) {
            File f = list.get(i);
            f.delete();
            new File(f.getPath().replaceFirst("\\.jpg$", ".txt")).delete();
        }
    }

    private static String enc(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8");
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    private static String sha1(String s) {
        try {
            byte[] d = MessageDigest.getInstance("SHA-1").digest(s.getBytes(UTF8));
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < 10; i++) {
                sb.append(String.format(Locale.ROOT, "%02x", d[i]));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            return Integer.toHexString(s.hashCode());
        }
    }

    private static String readText(File f) {
        if (!f.isFile()) {
            return "";
        }
        try (InputStream in = new FileInputStream(f)) {
            byte[] buf = new byte[(int) Math.min(f.length(), 4096)];
            int n = in.read(buf);
            return n <= 0 ? "" : new String(buf, 0, n, UTF8);
        } catch (IOException e) {
            return "";
        }
    }

    private static void writeText(File f, String s) {
        try (Writer w = new OutputStreamWriter(new FileOutputStream(f), UTF8)) {
            w.write(s);
        } catch (IOException ignored) {
            // The photo still shows, just without a credit line.
        }
    }
}
