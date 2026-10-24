package dev.tvhome;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapShader;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Outline;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.text.TextPaint;
import android.text.TextUtils;
import android.view.View;
import android.view.ViewOutlineProvider;

/** The rounded 16:9 picture of a tile: the app banner, or its icon on a tinted card. */
final class Art extends View {
    private final Entry entry;
    private final float radius;
    private final float density;
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private final Paint sheen = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint stroke = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF rect = new RectF();
    private final RectF inner = new RectF();
    private boolean highlighted;

    Art(Context c, Entry entry, final float radius) {
        super(c);
        this.entry = entry;
        this.radius = radius;
        this.density = c.getResources().getDisplayMetrics().density;
        stroke.setStyle(Paint.Style.STROKE);
        stroke.setStrokeWidth(2.5f * density);
        stroke.setColor(0xD9FFFFFF);
        setOutlineProvider(new ViewOutlineProvider() {
            @Override
            public void getOutline(View v, Outline o) {
                o.setRoundRect(0, 0, v.getWidth(), v.getHeight(), radius);
            }
        });
        setElevation(3 * density);
    }

    void setHighlighted(boolean on) {
        if (highlighted != on) {
            highlighted = on;
            invalidate();
        }
    }

    @Override
    protected void onSizeChanged(int w, int h, int oldw, int oldh) {
        if (w <= 0 || h <= 0) {
            return;
        }
        rect.set(0, 0, w, h);
        float half = stroke.getStrokeWidth() / 2f;
        inner.set(half, half, w - half, h - half);
        Bitmap art = entry.art;
        if (art == null || art.getWidth() != w || art.getHeight() != h) {
            art = render(getContext(), entry, w, h);
            entry.art = art;
        }
        paint.setShader(new BitmapShader(art, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP));
        sheen.setShader(new LinearGradient(0, 0, w * 0.75f, h, 0x38FFFFFF, 0x00FFFFFF,
                Shader.TileMode.CLAMP));
    }

    @Override
    protected void onDraw(Canvas c) {
        c.drawRoundRect(rect, radius, radius, paint);
        if (highlighted) {
            c.drawRoundRect(rect, radius, radius, sheen);
            c.drawRoundRect(inner, radius, radius, stroke);
        }
    }

    static Bitmap render(Context ctx, Entry e, int w, int h) {
        Bitmap b = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(b);
        if (e.banner != null) {
            c.drawColor(0xFF1C1C1E);
            drawFitted(c, e.banner, w, h, 1f);
            return b;
        }
        int base = e.kind == Entry.INPUT ? 0xFF3A4A5C : averageColor(e.icon);
        Paint bg = new Paint();
        bg.setShader(new LinearGradient(0, 0, 0, h, shade(base, 1.25f), shade(base, 0.7f),
                Shader.TileMode.CLAMP));
        c.drawRect(0, 0, w, h, bg);
        if (e.kind == Entry.INPUT || e.icon == null) {
            TextPaint tp = new TextPaint(Paint.ANTI_ALIAS_FLAG);
            tp.setColor(Color.WHITE);
            tp.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
            tp.setTextSize(h * 0.2f);
            tp.setTextAlign(Paint.Align.CENTER);
            CharSequence text = TextUtils.ellipsize(e.label, tp, w * 0.85f, TextUtils.TruncateAt.END);
            float y = h / 2f - (tp.descent() + tp.ascent()) / 2f;
            c.drawText(text, 0, text.length(), w / 2f, y, tp);
        } else {
            drawFitted(c, e.icon, w, h, 0.58f);
        }
        return b;
    }

    /** Draws d centred and scaled to fit inside fraction of the w x h box, keeping its aspect. */
    private static void drawFitted(Canvas c, Drawable d, int w, int h, float fraction) {
        int iw = d.getIntrinsicWidth();
        int ih = d.getIntrinsicHeight();
        if (iw <= 0 || ih <= 0) {
            iw = w;
            ih = h;
        }
        float scale = Math.min(w * fraction / iw, h * fraction / ih);
        if (fraction >= 1f) {
            // Banners: fill the whole card, cropping a sliver if the aspect is a little off.
            scale = Math.max(w / (float) iw, h / (float) ih);
        }
        int dw = Math.round(iw * scale);
        int dh = Math.round(ih * scale);
        int left = (w - dw) / 2;
        int top = (h - dh) / 2;
        d.setBounds(left, top, left + dw, top + dh);
        d.draw(c);
    }

    /** A calm card colour derived from the icon, so icon-only apps still look at home. */
    static int averageColor(Drawable icon) {
        if (icon == null) {
            return 0xFF34495E;
        }
        Bitmap small = Bitmap.createBitmap(12, 12, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(small);
        icon.setBounds(0, 0, 12, 12);
        icon.draw(c);
        long r = 0;
        long g = 0;
        long bl = 0;
        int n = 0;
        int[] px = new int[144];
        small.getPixels(px, 0, 12, 0, 0, 12, 12);
        small.recycle();
        for (int p : px) {
            if (Color.alpha(p) > 128) {
                r += Color.red(p);
                g += Color.green(p);
                bl += Color.blue(p);
                n++;
            }
        }
        if (n == 0) {
            return 0xFF34495E;
        }
        float[] hsv = new float[3];
        Color.RGBToHSV((int) (r / n), (int) (g / n), (int) (bl / n), hsv);
        hsv[1] = Math.min(hsv[1], 0.55f);
        hsv[2] = 0.42f;
        return Color.HSVToColor(hsv);
    }

    private static int shade(int color, float f) {
        float[] hsv = new float[3];
        Color.colorToHSV(color, hsv);
        hsv[2] = Math.max(0f, Math.min(1f, hsv[2] * f));
        return Color.HSVToColor(hsv);
    }
}
