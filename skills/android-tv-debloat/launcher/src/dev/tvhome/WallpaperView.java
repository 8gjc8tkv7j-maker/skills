package dev.tvhome;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.animation.ObjectAnimator;
import android.animation.PropertyValuesHolder;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.RenderEffect;
import android.graphics.Shader;
import android.os.Build;
import android.view.View;
import android.view.animation.LinearInterpolator;
import android.widget.FrameLayout;
import android.widget.ImageView;

import java.util.Random;

/** Two stacked photos that crossfade, with an optional slow drift ("Ken Burns") on the front one. */
final class WallpaperView extends FrameLayout {
    static final long FADE_MS = 1600;
    private static final float DRIFT_SCALE = 1.07f;

    private final ImageView a;
    private final ImageView b;
    private final Random random = new Random();
    private ImageView front;
    private Bitmap frontBmp;
    private ObjectAnimator drift;
    private ObjectAnimator fade;

    WallpaperView(Context c) {
        super(c);
        a = layer(c);
        b = layer(c);
        front = a;
    }

    private ImageView layer(Context c) {
        ImageView v = new ImageView(c);
        v.setScaleType(ImageView.ScaleType.CENTER_CROP);
        v.setAlpha(0f);
        addView(v, new LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT));
        return v;
    }

    void show(Bitmap bmp, boolean motion, long showMs) {
        final ImageView next = front == a ? b : a;
        final ImageView old = front;
        final Bitmap oldBmp = frontBmp;
        if (fade != null) {
            fade.end();
        }
        if (drift != null) {
            drift.cancel();
        }
        next.setImageBitmap(bmp);
        next.setScaleX(1f);
        next.setScaleY(1f);
        next.setAlpha(0f);
        next.bringToFront();
        front = next;
        frontBmp = bmp;

        fade = ObjectAnimator.ofFloat(next, View.ALPHA, 0f, 1f);
        fade.setDuration(oldBmp == null ? 900 : FADE_MS);
        fade.addListener(new AnimatorListenerAdapter() {
            @Override
            public void onAnimationEnd(Animator animation) {
                old.setAlpha(0f);
                old.setImageDrawable(null);
                if (oldBmp != null && oldBmp != frontBmp) {
                    oldBmp.recycle();
                }
            }
        });
        fade.start();

        if (motion) {
            int w = getWidth() > 0 ? getWidth() : getResources().getDisplayMetrics().widthPixels;
            int h = getHeight() > 0 ? getHeight() : getResources().getDisplayMetrics().heightPixels;
            next.setPivotX(w * (0.3f + 0.4f * random.nextFloat()));
            next.setPivotY(h * (0.35f + 0.3f * random.nextFloat()));
            drift = ObjectAnimator.ofPropertyValuesHolder(next,
                    PropertyValuesHolder.ofFloat(View.SCALE_X, 1f, DRIFT_SCALE),
                    PropertyValuesHolder.ofFloat(View.SCALE_Y, 1f, DRIFT_SCALE));
            drift.setInterpolator(new LinearInterpolator());
            drift.setDuration(showMs + FADE_MS);
            drift.start();
        }
    }

    /** Blurs the photo behind the app grid (Android 12+; older TVs just dim it). */
    void setBlurred(boolean on) {
        if (Build.VERSION.SDK_INT >= 31) {
            float r = 28 * getResources().getDisplayMetrics().density;
            setRenderEffect(on ? RenderEffect.createBlurEffect(r, r, Shader.TileMode.CLAMP) : null);
        }
    }

    void pauseMotion(boolean paused) {
        if (drift == null) {
            return;
        }
        if (paused) {
            drift.pause();
        } else {
            drift.resume();
        }
    }
}
