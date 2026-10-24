package dev.tvhome;

import android.app.Activity;
import android.app.ActivityOptions;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Rect;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.text.TextUtils;
import android.text.format.DateFormat;
import android.util.DisplayMetrics;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.animation.DecelerateInterpolator;
import android.view.animation.Interpolator;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextClock;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.Executor;
import java.util.concurrent.Executors;

/**
 * The home screen: a full-screen nature photo, a clock, and a glass dock of favourite apps.
 * Press down for every app, HDMI input and Settings; hold OK on a tile to move it or add it to the dock.
 */
public final class HomeActivity extends Activity implements Wallpapers.Listener {
    /** App list loading and photo decoding. Tests swap in a direct executor. */
    static Executor BG = Executors.newSingleThreadExecutor();

    private static final int COLUMNS = 5;
    private static final Interpolator EASE = new DecelerateInterpolator(1.8f);
    private static final int[] ROTATE_CHOICES = {1, 3, 5, 10, 30, 60};

    private float density;
    private int screenW;
    private int screenH;
    private int margin;
    private int gap;
    private int tileW;
    private int tileH;
    private int tileTotalH;
    private int panelPad;

    private FrameLayout root;
    private WallpaperView wallpaper;
    private View dim;
    private TextView caption;
    private LinearLayout clock;
    private FrameLayout dockLayer;
    private GlassPanel glass;
    private HorizontalScrollView dockScroll;
    private LinearLayout dockRow;
    private TextView hint;
    private TextView moveHint;
    private ScrollView appsLayer;
    private LinearLayout appsColumn;

    private Store store;
    private Wallpapers walls;
    private AppRepo.Catalog catalog = AppRepo.Catalog.EMPTY;
    private final Map<String, Entry> byId = new HashMap<>();
    private final ArrayList<String> dock = new ArrayList<>();
    private int generation;
    private boolean appsOpen;
    private boolean hasWallpaper;
    private String lastDockId;
    private Tile moving;
    private AlertDialog dialog;
    private BroadcastReceiver packageReceiver;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        DisplayMetrics dm = getResources().getDisplayMetrics();
        density = dm.density;
        screenW = Math.max(dm.widthPixels, dm.heightPixels);
        screenH = Math.min(dm.widthPixels, dm.heightPixels);
        margin = dp(64);
        gap = dp(26);
        tileW = (screenW - 2 * margin - (COLUMNS - 1) * gap) / COLUMNS;
        tileH = tileW * 9 / 16;
        tileTotalH = tileH + dp(44);
        panelPad = dp(20);

        store = new Store(this);
        buildViews();
        setContentView(root);
        applyDisplaySettings();
        walls = new Wallpapers(this, store, screenW, screenH, this);
        handleExtras(getIntent(), false);
        registerPackageReceiver();
        reloadApps();
    }

    @Override
    protected void onStart() {
        super.onStart();
        walls.start();
        wallpaper.pauseMotion(appsOpen);
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshFromFiles();
    }

    @Override
    protected void onStop() {
        walls.stop();
        wallpaper.pauseMotion(true);
        endMove();
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        if (packageReceiver != null) {
            unregisterReceiver(packageReceiver);
        }
        walls.stop();
        super.onDestroy();
    }

    /** Pressing Home while already home: back to the dock, first tile. */
    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (dialog != null && dialog.isShowing()) {
            dialog.dismiss();
        }
        endMove();
        handleExtras(intent, true);
        refreshFromFiles();
        if (appsOpen) {
            closeApps(null);
        } else {
            dockScroll.smoothScrollTo(0, 0);
            focusDock(null);
        }
    }

    /**
     * Lets tvctl.py set things up from the computer:
     * {@code am start -n dev.tvhome/.HomeActivity --es dock pkg1,pkg2,settings --ez next_wallpaper true}.
     */
    private void handleExtras(Intent intent, boolean running) {
        if (intent == null) {
            return;
        }
        String dockExtra = intent.getStringExtra("dock");
        if (dockExtra != null) {
            ArrayList<String> ids = new ArrayList<>();
            for (String s : dockExtra.split(",")) {
                s = s.trim();
                if (!s.isEmpty() && !ids.contains(s)) {
                    ids.add(s);
                }
            }
            store.writeDock(ids);
            dock.clear();
            dock.addAll(ids);
            if (!byId.isEmpty()) {
                buildDock();
            }
            intent.removeExtra("dock");
        }
        if (running && intent.getBooleanExtra("next_wallpaper", false)) {
            walls.reset();
            walls.next();
        }
        intent.removeExtra("next_wallpaper");
    }

    @Override
    public void onBackPressed() {
        if (appsOpen) {
            closeApps(lastDockId);
        }
        // Back on the home screen itself goes nowhere.
    }

    // ---------------------------------------------------------------- views

    private void buildViews() {
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.BLACK);

        wallpaper = new WallpaperView(this);
        root.addView(wallpaper, match());

        View scrim = new View(this);
        scrim.setBackground(new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[] {0x66000000, 0x0D000000, 0x00000000, 0x1A000000, 0x8C000000}));
        root.addView(scrim, match());

        dim = new View(this);
        dim.setBackgroundColor(0x8C000000);
        dim.setAlpha(0f);
        root.addView(dim, match());

        caption = text(14, 0xCCFFFFFF, "sans-serif");
        caption.setSingleLine(true);
        caption.setEllipsize(TextUtils.TruncateAt.END);
        FrameLayout.LayoutParams clp = new FrameLayout.LayoutParams(screenW / 2,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP | Gravity.START);
        clp.leftMargin = margin;
        clp.topMargin = dp(52);
        root.addView(caption, clp);

        clock = new LinearLayout(this);
        clock.setOrientation(LinearLayout.VERTICAL);
        clock.setGravity(Gravity.END);
        TextClock time = new TextClock(this);
        time.setFormat12Hour("h:mm");
        time.setFormat24Hour("HH:mm");
        style(time, 44, Color.WHITE, "sans-serif-light");
        TextClock date = new TextClock(this);
        String pattern = DateFormat.getBestDateTimePattern(Locale.getDefault(), "EEEEdMMMM");
        date.setFormat12Hour(pattern);
        date.setFormat24Hour(pattern);
        style(date, 16, 0xD9FFFFFF, "sans-serif");
        clock.addView(time);
        clock.addView(date);
        FrameLayout.LayoutParams tlp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP | Gravity.END);
        tlp.rightMargin = margin;
        tlp.topMargin = dp(36);
        root.addView(clock, tlp);

        dockLayer = new FrameLayout(this);
        dockLayer.setClipChildren(false);
        glass = new GlassPanel(this, dp(26), screenW, screenH);
        dockLayer.addView(glass, new FrameLayout.LayoutParams(0, 0));
        dockScroll = new HorizontalScrollView(this);
        dockScroll.setHorizontalScrollBarEnabled(false);
        dockScroll.setOverScrollMode(View.OVER_SCROLL_NEVER);
        dockScroll.setHorizontalFadingEdgeEnabled(true);
        dockScroll.setFadingEdgeLength(dp(40));
        dockRow = new LinearLayout(this);
        dockRow.setOrientation(LinearLayout.HORIZONTAL);
        dockRow.setPadding(panelPad, panelPad, panelPad, 0);
        dockRow.setClipChildren(false);
        dockRow.setClipToPadding(false);
        dockScroll.addView(dockRow, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        dockLayer.addView(dockScroll, new FrameLayout.LayoutParams(0, 0));
        FrameLayout.LayoutParams dlp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM);
        dlp.bottomMargin = dp(56);
        root.addView(dockLayer, dlp);

        hint = text(14, 0x99FFFFFF, "sans-serif-medium");
        hint.setText("▾  " + getString(R.string.all_apps_hint));
        root.addView(hint, bottomCenter(dp(22)));

        moveHint = text(15, Color.WHITE, "sans-serif-medium");
        moveHint.setText(R.string.move_hint);
        moveHint.setVisibility(View.GONE);
        root.addView(moveHint, bottomCenter(dp(22)));

        appsLayer = new ScrollView(this);
        appsLayer.setVerticalScrollBarEnabled(false);
        appsLayer.setOverScrollMode(View.OVER_SCROLL_NEVER);
        appsLayer.setVerticalFadingEdgeEnabled(true);
        appsLayer.setFadingEdgeLength(dp(56));
        appsLayer.setVisibility(View.GONE);
        appsLayer.setAlpha(0f);
        appsColumn = new LinearLayout(this);
        appsColumn.setOrientation(LinearLayout.VERTICAL);
        appsColumn.setPadding(margin, dp(72), margin, dp(72));
        appsColumn.setClipChildren(false);
        appsColumn.setClipToPadding(false);
        appsLayer.addView(appsColumn, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(appsLayer, match());
    }

    private void buildDock() {
        dockRow.removeAllViews();
        int n = 0;
        for (String id : dock) {
            Entry e = byId.get(id);
            if (e == null) {
                continue; // Disabled or uninstalled: keep its place in dock.txt, show nothing.
            }
            dockRow.addView(makeTile(e, true), dockParams(n));
            n++;
        }
        int content = n * tileW + Math.max(0, n - 1) * gap + 2 * panelPad;
        int max = screenW - 2 * (margin - panelPad);
        int w = Math.max(tileW + 2 * panelPad, Math.min(content, max));
        int h = panelPad + tileTotalH;
        glass.setLayoutParams(new FrameLayout.LayoutParams(w, h, Gravity.CENTER_HORIZONTAL | Gravity.BOTTOM));
        dockScroll.setLayoutParams(new FrameLayout.LayoutParams(w, h, Gravity.CENTER_HORIZONTAL | Gravity.BOTTOM));
    }

    private LinearLayout.LayoutParams dockParams(int index) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(tileW, tileTotalH);
        lp.leftMargin = index == 0 ? 0 : gap;
        return lp;
    }

    private void buildGrid() {
        appsColumn.removeAllViews();
        int row = addSection(getString(R.string.apps), catalog.apps, 0);
        row = addSection(getString(R.string.inputs), catalog.inputs, row);
        addSection(getString(R.string.system), catalog.system, row);
    }

    private int addSection(String title, List<Entry> entries, int firstRow) {
        if (entries.isEmpty()) {
            return firstRow;
        }
        TextView header = text(20, 0xE6FFFFFF, "sans-serif-medium");
        header.setText(title);
        LinearLayout.LayoutParams hlp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        hlp.topMargin = firstRow == 0 ? 0 : dp(18);
        hlp.bottomMargin = dp(16);
        appsColumn.addView(header, hlp);
        LinearLayout line = null;
        for (int i = 0; i < entries.size(); i++) {
            if (i % COLUMNS == 0) {
                line = new LinearLayout(this);
                line.setOrientation(LinearLayout.HORIZONTAL);
                line.setClipChildren(false);
                appsColumn.addView(line, new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, tileTotalH));
            }
            Tile t = makeTile(entries.get(i), false);
            t.row = firstRow + i / COLUMNS;
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(tileW, tileTotalH);
            lp.leftMargin = i % COLUMNS == 0 ? 0 : gap;
            line.addView(t, lp);
        }
        return firstRow + (entries.size() + COLUMNS - 1) / COLUMNS;
    }

    private Tile makeTile(Entry e, boolean inDock) {
        final Tile t = new Tile(this, e, tileW, tileH, inDock);
        t.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                if (moving == null) {
                    launch(t);
                }
            }
        });
        t.setOnLongClickListener(new View.OnLongClickListener() {
            @Override
            public boolean onLongClick(View v) {
                if (moving == null) {
                    showOptions(t);
                }
                return true;
            }
        });
        return t;
    }

    // ---------------------------------------------------------------- apps & files

    private void registerPackageReceiver() {
        packageReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                reloadApps();
            }
        };
        IntentFilter f = new IntentFilter();
        f.addAction(Intent.ACTION_PACKAGE_ADDED);
        f.addAction(Intent.ACTION_PACKAGE_REMOVED);
        f.addAction(Intent.ACTION_PACKAGE_CHANGED);
        f.addAction(Intent.ACTION_PACKAGE_REPLACED);
        f.addDataScheme("package");
        registerReceiver(packageReceiver, f);
    }

    private void reloadApps() {
        final int gen = ++generation;
        final Context app = getApplicationContext();
        BG.execute(new Runnable() {
            @Override
            public void run() {
                final AppRepo.Catalog c = AppRepo.load(app);
                store.writeApps(c.all());
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        if (gen == generation && !isDestroyed()) {
                            apply(c);
                        }
                    }
                });
            }
        });
    }

    private void apply(AppRepo.Catalog c) {
        View f = focusedView();
        String focusId = f instanceof Tile ? ((Tile) f).entry.id : null;
        endMove();
        catalog = c;
        byId.clear();
        for (Entry e : c.all()) {
            byId.put(e.id, e);
        }
        dock.clear();
        if (store.hasDock()) {
            dock.addAll(store.readDock());
        } else {
            dock.addAll(AppRepo.defaultDock(c));
            store.writeDock(dock);
        }
        buildDock();
        buildGrid();
        if (appsOpen) {
            focusGrid(focusId);
        } else {
            focusDock(focusId);
        }
    }

    /** Picks up dock.txt / home.properties pushed over adb while we were away. */
    private void refreshFromFiles() {
        if (store.propsChanged()) {
            store.loadProps();
            applyDisplaySettings();
            walls.restartTimer();
        }
        if (store.dockChanged() && !byId.isEmpty()) {
            dock.clear();
            dock.addAll(store.readDock());
            buildDock();
            if (!appsOpen) {
                focusDock(null);
            }
        }
    }

    private void applyDisplaySettings() {
        clock.setVisibility(store.clock() ? View.VISIBLE : View.GONE);
    }

    // ---------------------------------------------------------------- navigation

    @Override
    public boolean dispatchKeyEvent(KeyEvent ev) {
        int code = ev.getKeyCode();
        boolean down = ev.getAction() == KeyEvent.ACTION_DOWN;
        if (moving != null) {
            if (code == KeyEvent.KEYCODE_DPAD_LEFT || code == KeyEvent.KEYCODE_DPAD_RIGHT) {
                if (down) {
                    shift(code == KeyEvent.KEYCODE_DPAD_LEFT ? -1 : 1);
                }
                return true;
            }
            if (isOk(code) || code == KeyEvent.KEYCODE_BACK || code == KeyEvent.KEYCODE_ESCAPE) {
                if (down) {
                    endMove();
                }
                return true;
            }
            if (code == KeyEvent.KEYCODE_DPAD_UP || code == KeyEvent.KEYCODE_DPAD_DOWN) {
                return true;
            }
        }
        if (down) {
            View f = focusedView();
            Tile tile = f instanceof Tile ? (Tile) f : null;
            switch (code) {
                case KeyEvent.KEYCODE_DPAD_DOWN:
                    if (!appsOpen && (tile == null || tile.inDock)) {
                        openApps(tile);
                        return true;
                    }
                    break;
                case KeyEvent.KEYCODE_DPAD_UP:
                    if (appsOpen && (tile == null || tile.row == 0)) {
                        closeApps(lastDockId);
                        return true;
                    }
                    break;
                case KeyEvent.KEYCODE_BACK:
                case KeyEvent.KEYCODE_ESCAPE:
                    if (appsOpen) {
                        closeApps(lastDockId);
                    }
                    return true;
                case KeyEvent.KEYCODE_MENU:
                    if (tile != null) {
                        showOptions(tile);
                        return true;
                    }
                    break;
                default:
                    break;
            }
        } else if (code == KeyEvent.KEYCODE_BACK || code == KeyEvent.KEYCODE_ESCAPE) {
            return true;
        }
        return super.dispatchKeyEvent(ev);
    }

    private static boolean isOk(int code) {
        return code == KeyEvent.KEYCODE_DPAD_CENTER || code == KeyEvent.KEYCODE_ENTER
                || code == KeyEvent.KEYCODE_NUMPAD_ENTER;
    }

    private void openApps(Tile from) {
        if (appsOpen) {
            return;
        }
        appsOpen = true;
        lastDockId = from == null ? null : from.entry.id;
        dockRow.setDescendantFocusability(ViewGroup.FOCUS_BLOCK_DESCENDANTS);
        appsLayer.setVisibility(View.VISIBLE);
        appsLayer.scrollTo(0, 0);
        appsLayer.setTranslationY(dp(56));
        appsLayer.animate().alpha(1f).translationY(0).setDuration(280).setInterpolator(EASE).start();
        dockLayer.animate().alpha(0f).translationY(-dp(28)).setDuration(220).setInterpolator(EASE).start();
        hint.animate().alpha(0f).setDuration(150).start();
        caption.animate().alpha(0f).setDuration(150).start();
        clock.animate().alpha(0f).setDuration(150).start();
        dim.animate().alpha(1f).setDuration(280).start();
        wallpaper.setBlurred(true);
        wallpaper.pauseMotion(true);
        focusGrid(null);
    }

    private void closeApps(String focusId) {
        if (!appsOpen) {
            return;
        }
        appsOpen = false;
        dockRow.setDescendantFocusability(ViewGroup.FOCUS_AFTER_DESCENDANTS);
        appsLayer.animate().alpha(0f).translationY(dp(56)).setDuration(220).setInterpolator(EASE)
                .withEndAction(new Runnable() {
                    @Override
                    public void run() {
                        if (!appsOpen) {
                            appsLayer.setVisibility(View.GONE);
                        }
                    }
                }).start();
        dockLayer.animate().alpha(1f).translationY(0).setDuration(280).setInterpolator(EASE).start();
        hint.animate().alpha(1f).setDuration(280).start();
        caption.animate().alpha(1f).setDuration(280).start();
        clock.animate().alpha(1f).setDuration(280).start();
        dim.animate().alpha(0f).setDuration(280).start();
        wallpaper.setBlurred(false);
        wallpaper.pauseMotion(false);
        focusDock(focusId);
    }

    private void focusDock(String id) {
        Tile target = null;
        for (int i = 0; i < dockRow.getChildCount(); i++) {
            Tile t = (Tile) dockRow.getChildAt(i);
            if (target == null || t.entry.id.equals(id)) {
                target = t;
                if (t.entry.id.equals(id)) {
                    break;
                }
            }
        }
        if (target != null) {
            target.requestFocus();
        }
    }

    private void focusGrid(String id) {
        Tile target = null;
        for (int i = 0; i < appsColumn.getChildCount(); i++) {
            View child = appsColumn.getChildAt(i);
            if (!(child instanceof LinearLayout)) {
                continue;
            }
            LinearLayout line = (LinearLayout) child;
            for (int j = 0; j < line.getChildCount(); j++) {
                Tile t = (Tile) line.getChildAt(j);
                if (target == null) {
                    target = t;
                }
                if (t.entry.id.equals(id)) {
                    t.requestFocus();
                    return;
                }
            }
        }
        if (target != null) {
            target.requestFocus();
        }
    }

    // ---------------------------------------------------------------- actions

    private void launch(Tile t) {
        Entry e = t.entry;
        if (e.kind == Entry.HOME_SETTINGS) {
            showHomeSettings();
            return;
        }
        if (e.intent == null) {
            return;
        }
        try {
            Bundle opts = ActivityOptions.makeScaleUpAnimation(
                    t.art, 0, 0, t.art.getWidth(), t.art.getHeight()).toBundle();
            startActivity(e.intent, opts);
        } catch (ActivityNotFoundException | SecurityException ex) {
            Toast.makeText(this, getString(R.string.cant_open, e.label), Toast.LENGTH_SHORT).show();
        }
    }

    private void showOptions(final Tile t) {
        final Entry e = t.entry;
        final boolean inDock = dock.contains(e.id);
        ArrayList<CharSequence> items = new ArrayList<>();
        final ArrayList<Runnable> actions = new ArrayList<>();
        if (t.inDock) {
            items.add(getString(R.string.move));
            actions.add(new Runnable() {
                @Override
                public void run() {
                    startMove(t);
                }
            });
        }
        if (e.kind != Entry.HOME_SETTINGS) {
            items.add(getString(inDock ? R.string.remove_from_dock : R.string.add_to_dock));
            actions.add(new Runnable() {
                @Override
                public void run() {
                    toggleDock(t, inDock);
                }
            });
        }
        if (e.kind == Entry.APP) {
            items.add(getString(R.string.app_info));
            actions.add(new Runnable() {
                @Override
                public void run() {
                    Intent info = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                            Uri.fromParts("package", e.id, null)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    try {
                        startActivity(info);
                    } catch (ActivityNotFoundException ignored) {
                        Toast.makeText(HomeActivity.this, getString(R.string.cant_open, e.label),
                                Toast.LENGTH_SHORT).show();
                    }
                }
            });
        }
        if (items.isEmpty()) {
            return;
        }
        showList(e.label, items.toArray(new CharSequence[0]), actions);
    }

    private void toggleDock(Tile t, boolean inDock) {
        String id = t.entry.id;
        if (inDock) {
            int index = dockRow.indexOfChild(t);
            dock.remove(id);
            store.writeDock(dock);
            buildDock();
            if (t.inDock && dockRow.getChildCount() > 0) {
                dockRow.getChildAt(Math.min(index, dockRow.getChildCount() - 1)).requestFocus();
            }
        } else {
            dock.add(id);
            store.writeDock(dock);
            buildDock();
            Toast.makeText(this, R.string.added_to_dock, Toast.LENGTH_SHORT).show();
        }
    }

    private void startMove(Tile t) {
        moving = t;
        t.setMoving(true);
        t.requestFocus();
        hint.setVisibility(View.INVISIBLE);
        moveHint.setVisibility(View.VISIBLE);
    }

    private void endMove() {
        if (moving == null) {
            return;
        }
        moving.setMoving(false);
        moving = null;
        moveHint.setVisibility(View.GONE);
        hint.setVisibility(View.VISIBLE);
        store.writeDock(dock);
    }

    /** Swaps the moving tile with its neighbour; the neighbour is the view that moves, so focus stays. */
    private void shift(int delta) {
        int from = dockRow.indexOfChild(moving);
        int to = from + delta;
        if (from < 0 || to < 0 || to >= dockRow.getChildCount()) {
            return;
        }
        Tile other = (Tile) dockRow.getChildAt(to);
        Collections.swap(dock, dock.indexOf(moving.entry.id), dock.indexOf(other.entry.id));
        dockRow.removeView(other);
        dockRow.addView(other, from);
        for (int i = 0; i < dockRow.getChildCount(); i++) {
            dockRow.getChildAt(i).setLayoutParams(dockParams(i));
        }
        final Tile m = moving;
        m.post(new Runnable() {
            @Override
            public void run() {
                m.requestRectangleOnScreen(new Rect(0, 0, m.getWidth(), m.getHeight()), false);
            }
        });
    }

    private void showHomeSettings() {
        final int minutes = store.rotateMinutes();
        final boolean online = store.onlinePhotos();
        final boolean motion = store.motion();
        final boolean clockOn = store.clock();
        CharSequence[] items = {
            getString(R.string.next_wallpaper),
            getString(R.string.rotate_every, minutes),
            getString(online ? R.string.online_photos_on : R.string.online_photos_off),
            getString(motion ? R.string.motion_on : R.string.motion_off),
            getString(clockOn ? R.string.clock_on : R.string.clock_off),
            getString(R.string.reset_dock),
        };
        ArrayList<Runnable> actions = new ArrayList<>();
        actions.add(new Runnable() {
            @Override
            public void run() {
                walls.next();
            }
        });
        actions.add(new Runnable() {
            @Override
            public void run() {
                chooseRotation();
            }
        });
        actions.add(new Runnable() {
            @Override
            public void run() {
                store.put("online_photos", String.valueOf(!online));
                walls.stop();
                walls.start();
            }
        });
        actions.add(new Runnable() {
            @Override
            public void run() {
                store.put("motion", String.valueOf(!motion));
                walls.next();
            }
        });
        actions.add(new Runnable() {
            @Override
            public void run() {
                store.put("clock", String.valueOf(!clockOn));
                applyDisplaySettings();
            }
        });
        actions.add(new Runnable() {
            @Override
            public void run() {
                dock.clear();
                dock.addAll(AppRepo.defaultDock(catalog));
                store.writeDock(dock);
                buildDock();
            }
        });
        showList(getString(R.string.home_settings), items, actions);
    }

    private void chooseRotation() {
        CharSequence[] items = new CharSequence[ROTATE_CHOICES.length];
        ArrayList<Runnable> actions = new ArrayList<>();
        for (int i = 0; i < ROTATE_CHOICES.length; i++) {
            final int m = ROTATE_CHOICES[i];
            items[i] = getString(R.string.every_minutes, m);
            actions.add(new Runnable() {
                @Override
                public void run() {
                    store.put("rotate_minutes", String.valueOf(m));
                    walls.restartTimer();
                }
            });
        }
        showList(getString(R.string.home_settings), items, actions);
    }

    private void showList(CharSequence title, CharSequence[] items, final List<Runnable> actions) {
        if (dialog != null && dialog.isShowing()) {
            dialog.dismiss();
        }
        dialog = new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle(title)
                .setItems(items, new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface d, int which) {
                        actions.get(which).run();
                    }
                })
                .create();
        dialog.show();
        if (dialog.getWindow() != null) {
            dialog.getWindow().setLayout(Math.min(dp(520), screenW - 2 * margin),
                    ViewGroup.LayoutParams.WRAP_CONTENT);
        }
    }

    // ---------------------------------------------------------------- wallpaper

    @Override
    public void onWallpaper(Bitmap full, Bitmap blurred, final String text) {
        boolean first = !hasWallpaper;
        hasWallpaper = true;
        wallpaper.show(full, store.motion(), store.rotateMinutes() * 60_000L);
        glass.setBackdrop(blurred, !first);
        caption.animate().alpha(0f).setDuration(first ? 0 : 500).withEndAction(new Runnable() {
            @Override
            public void run() {
                caption.setText(text);
                if (!appsOpen) {
                    caption.animate().alpha(1f).setDuration(700).start();
                }
            }
        }).start();
    }

    // ---------------------------------------------------------------- helpers

    /** The focused view even when the window itself hasn't got focus yet (e.g. during start-up). */
    private View focusedView() {
        return getWindow().getDecorView().findFocus();
    }

    private int dp(float v) {
        return Math.round(v * density);
    }

    private static FrameLayout.LayoutParams match() {
        return new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
    }

    private static FrameLayout.LayoutParams bottomCenter(int bottomMargin) {
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        lp.bottomMargin = bottomMargin;
        return lp;
    }

    private TextView text(float sp, int color, String family) {
        TextView t = new TextView(this);
        style(t, sp, color, family);
        return t;
    }

    private void style(TextView t, float sp, int color, String family) {
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        t.setTypeface(Typeface.create(family, Typeface.NORMAL));
        t.setShadowLayer(dp(6), 0, dp(1), 0x99000000);
    }
}
