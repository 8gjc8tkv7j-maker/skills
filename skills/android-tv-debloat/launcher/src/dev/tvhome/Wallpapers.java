package dev.tvhome;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.Executor;
import java.util.concurrent.Executors;

/**
 * Picks the next wallpaper and decodes it off the main thread. Photos come from three places:
 * your own pictures pushed to files/wallpapers, nature photos cached from Wikimedia Commons, and
 * the built-in set in assets/wallpapers (used when the other two have fewer than four pictures).
 */
final class Wallpapers {
    interface Listener {
        void onWallpaper(Bitmap full, Bitmap blurred, String caption);
    }

    static final class Pic {
        final String key;
        final String asset;
        final File file;
        final String caption;

        Pic(String key, String asset, File file, String caption) {
            this.key = key;
            this.asset = asset;
            this.file = file;
            this.caption = caption;
        }
    }

    /** Network work never blocks decoding or app loading. */
    static Executor NET = Executors.newSingleThreadExecutor();

    private final Context ctx;
    private final Store store;
    private final int screenW;
    private final int screenH;
    private final Listener listener;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ArrayList<Pic> bag = new ArrayList<>();
    private String lastKey;
    private boolean running;
    private boolean loading;
    private long shownAt = -1;
    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            next();
        }
    };

    Wallpapers(Context ctx, Store store, int screenW, int screenH, Listener listener) {
        this.ctx = ctx.getApplicationContext();
        this.store = store;
        this.screenW = screenW;
        this.screenH = screenH;
        this.listener = listener;
    }

    void start() {
        running = true;
        long interval = store.rotateMinutes() * 60_000L;
        long shown = shownAt < 0 ? Long.MAX_VALUE : SystemClock.elapsedRealtime() - shownAt;
        if (shownAt < 0 || shown >= interval) {
            next();
        } else {
            main.postDelayed(tick, interval - shown);
        }
        if (store.onlinePhotos()) {
            NET.execute(new Runnable() {
                @Override
                public void run() {
                    PhotoFetcher.maybeFetch(ctx);
                }
            });
        }
    }

    void stop() {
        running = false;
        main.removeCallbacks(tick);
    }

    /** Forget the shuffled order so newly added pictures are picked up on the next change. */
    void reset() {
        bag.clear();
    }

    void restartTimer() {
        main.removeCallbacks(tick);
        if (running) {
            main.postDelayed(tick, store.rotateMinutes() * 60_000L);
        }
    }

    void next() {
        main.removeCallbacks(tick);
        if (loading) {
            return;
        }
        loading = true;
        HomeActivity.BG.execute(new Runnable() {
            @Override
            public void run() {
                Pic pic = null;
                Bitmap full = null;
                for (int attempt = 0; attempt < 3 && full == null; attempt++) {
                    pic = pick();
                    if (pic == null) {
                        break;
                    }
                    full = decode(pic);
                }
                final Pic shown = pic;
                final Bitmap fullBmp = full;
                final Bitmap blurred = full == null ? null : Blur.backdrop(full);
                main.post(new Runnable() {
                    @Override
                    public void run() {
                        loading = false;
                        if (fullBmp != null) {
                            lastKey = shown.key;
                            shownAt = SystemClock.elapsedRealtime();
                            listener.onWallpaper(fullBmp, blurred, shown.caption);
                        }
                        restartTimer();
                    }
                });
            }
        });
    }

    private Pic pick() {
        if (bag.isEmpty()) {
            List<Pic> pool = pool();
            if (pool.isEmpty()) {
                return null;
            }
            bag.addAll(pool);
            Collections.shuffle(bag);
            if (bag.size() > 1 && bag.get(0).key.equals(lastKey)) {
                Collections.swap(bag, 0, bag.size() - 1);
            }
        }
        return bag.remove(0);
    }

    private List<Pic> pool() {
        ArrayList<Pic> pool = new ArrayList<>();
        File[] mine = store.wallpaperDir().listFiles();
        if (mine != null) {
            for (File f : mine) {
                String n = f.getName().toLowerCase(Locale.ROOT);
                if (f.isFile() && (n.endsWith(".jpg") || n.endsWith(".jpeg") || n.endsWith(".png")
                        || n.endsWith(".webp"))) {
                    pool.add(new Pic(f.getPath(), null, f, ""));
                }
            }
        }
        if (store.onlinePhotos()) {
            pool.addAll(PhotoFetcher.cached(ctx));
        }
        if (pool.size() < 4) {
            pool.addAll(builtIn());
        }
        return pool;
    }

    private List<Pic> builtIn() {
        ArrayList<Pic> out = new ArrayList<>();
        try {
            String[] names = ctx.getAssets().list("wallpapers");
            if (names != null) {
                for (String n : names) {
                    if (n.endsWith(".jpg")) {
                        out.add(new Pic("asset:" + n, "wallpapers/" + n, null, prettyName(n)));
                    }
                }
            }
        } catch (IOException ignored) {
            // No built-in wallpapers.
        }
        return out;
    }

    /** "03-misty-forest.jpg" -> "Misty forest". */
    static String prettyName(String file) {
        String s = file.replaceFirst("\\.[A-Za-z]+$", "").replaceFirst("^\\d+[-_ ]*", "").replace('-', ' ');
        return s.isEmpty() ? s : Character.toUpperCase(s.charAt(0)) + s.substring(1);
    }

    private Bitmap decode(Pic p) {
        try {
            BitmapFactory.Options bounds = new BitmapFactory.Options();
            bounds.inJustDecodeBounds = true;
            try (InputStream in = open(p)) {
                BitmapFactory.decodeStream(in, null, bounds);
            }
            if (bounds.outWidth <= 0 || bounds.outHeight <= 0) {
                return null;
            }
            int sample = 1;
            while (bounds.outWidth / (sample * 2) >= screenW && bounds.outHeight / (sample * 2) >= screenH) {
                sample *= 2;
            }
            BitmapFactory.Options o = new BitmapFactory.Options();
            o.inSampleSize = sample;
            o.inPreferredConfig = Bitmap.Config.ARGB_8888;
            try (InputStream in = open(p)) {
                return BitmapFactory.decodeStream(in, null, o);
            }
        } catch (IOException | OutOfMemoryError e) {
            return null;
        }
    }

    private InputStream open(Pic p) throws IOException {
        return p.asset != null ? ctx.getAssets().open(p.asset) : new FileInputStream(p.file);
    }
}
