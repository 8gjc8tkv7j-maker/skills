package dev.tvhome;

import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapShader;
import android.graphics.Canvas;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Shader;
import android.view.View;

/**
 * Frosted-glass shelf behind the dock. Android views can't blur what is behind them, so this draws
 * a tiny pre-blurred copy of the wallpaper, stretched and lined up with the real one.
 */
final class GlassPanel extends View {
    private final float radius;
    private final int screenW;
    private final int screenH;
    private final Paint cur = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private final Paint prev = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private final Paint tint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint edge = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Matrix matrix = new Matrix();
    private final RectF rect = new RectF();
    private final RectF edgeRect = new RectF();
    private final int[] loc = new int[2];
    private final int[] rootLoc = new int[2];
    private Bitmap curBmp;
    private Bitmap prevBmp;
    private float mix = 1f;
    private ValueAnimator fade;

    GlassPanel(Context c, float radius, int screenW, int screenH) {
        super(c);
        this.radius = radius;
        this.screenW = screenW;
        this.screenH = screenH;
        float d = c.getResources().getDisplayMetrics().density;
        tint.setColor(0x33FFFFFF);
        edge.setStyle(Paint.Style.STROKE);
        edge.setStrokeWidth(d);
        edge.setColor(0x40FFFFFF);
    }

    void setBackdrop(Bitmap blurred, boolean animate) {
        if (fade != null) {
            fade.cancel();
        }
        prevBmp = animate ? curBmp : null;
        if (prevBmp != null) {
            prev.setShader(new BitmapShader(prevBmp, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP));
        }
        curBmp = blurred;
        cur.setShader(blurred == null ? null
                : new BitmapShader(blurred, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP));
        mix = animate ? 0f : 1f;
        if (animate) {
            fade = ValueAnimator.ofFloat(0f, 1f);
            fade.setDuration(WallpaperView.FADE_MS);
            fade.addUpdateListener(new ValueAnimator.AnimatorUpdateListener() {
                @Override
                public void onAnimationUpdate(ValueAnimator a) {
                    mix = (Float) a.getAnimatedValue();
                    invalidate();
                }
            });
            fade.start();
        }
        invalidate();
    }

    @Override
    protected void onDraw(Canvas c) {
        rect.set(0, 0, getWidth(), getHeight());
        float half = edge.getStrokeWidth() / 2f;
        edgeRect.set(half, half, getWidth() - half, getHeight() - half);
        getLocationInWindow(loc);
        getRootView().getLocationInWindow(rootLoc);
        float ox = loc[0] - rootLoc[0];
        float oy = loc[1] - rootLoc[1];
        if (prevBmp != null && mix < 1f) {
            drawBackdrop(c, prevBmp, prev, 255, ox, oy);
        }
        if (curBmp != null) {
            drawBackdrop(c, curBmp, cur, Math.round(255 * mix), ox, oy);
        }
        c.drawRoundRect(rect, radius, radius, tint);
        c.drawRoundRect(edgeRect, radius, radius, edge);
    }

    private void drawBackdrop(Canvas c, Bitmap b, Paint p, int alpha, float ox, float oy) {
        float s = Math.max(screenW / (float) b.getWidth(), screenH / (float) b.getHeight());
        matrix.setScale(s, s);
        matrix.postTranslate((screenW - b.getWidth() * s) / 2f - ox, (screenH - b.getHeight() * s) / 2f - oy);
        p.getShader().setLocalMatrix(matrix);
        p.setAlpha(alpha);
        c.drawRoundRect(rect, radius, radius, p);
    }
}
