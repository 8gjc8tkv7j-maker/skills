package dev.tvhome;

import android.animation.ObjectAnimator;
import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Color;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.animation.DecelerateInterpolator;
import android.view.animation.Interpolator;
import android.widget.FrameLayout;
import android.widget.TextView;

/** A focusable tile: artwork that lifts when focused, with the app name underneath. */
final class Tile extends FrameLayout implements View.OnFocusChangeListener {
    static final float FOCUS_SCALE = 1.12f;
    private static final Interpolator EASE = new DecelerateInterpolator(1.6f);

    final Entry entry;
    final boolean inDock;
    final Art art;
    /** Row index in the app grid (0 = first row); unused in the dock. */
    int row;

    private final TextView label;
    private final int artH;
    private final float density;
    private ObjectAnimator wiggle;

    Tile(Context c, Entry entry, int w, int h, boolean inDock) {
        super(c);
        this.entry = entry;
        this.inDock = inDock;
        this.artH = h;
        this.density = c.getResources().getDisplayMetrics().density;
        setFocusable(true);
        setClipChildren(false);
        setClipToPadding(false);
        setContentDescription(entry.label);

        art = new Art(c, entry, 12 * density);
        addView(art, new LayoutParams(w, h, Gravity.TOP | Gravity.CENTER_HORIZONTAL));

        label = new TextView(c);
        label.setText(entry.label);
        label.setTextColor(Color.WHITE);
        label.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        label.setSingleLine(true);
        label.setEllipsize(TextUtils.TruncateAt.END);
        label.setGravity(Gravity.CENTER);
        label.setShadowLayer(6 * density, 0, density, 0xB3000000);
        label.setAlpha(0f);
        LayoutParams lp = new LayoutParams(w, LayoutParams.WRAP_CONTENT,
                Gravity.TOP | Gravity.CENTER_HORIZONTAL);
        lp.topMargin = h + Math.round(10 * density);
        addView(label, lp);

        setOnFocusChangeListener(this);
    }

    @Override
    public void onFocusChange(View v, boolean has) {
        float s = has ? FOCUS_SCALE : 1f;
        art.animate().scaleX(s).scaleY(s).translationZ(has ? 18 * density : 0)
                .setDuration(180).setInterpolator(EASE).start();
        label.animate().alpha(has ? 1f : 0f).translationY(has ? artH * (s - 1f) / 2f : 0)
                .setDuration(180).setInterpolator(EASE).start();
        art.setHighlighted(has);
    }

    void setMoving(boolean on) {
        if (wiggle != null) {
            wiggle.cancel();
            wiggle = null;
        }
        art.setRotation(0);
        if (on) {
            wiggle = ObjectAnimator.ofFloat(art, View.ROTATION, -1.6f, 1.6f);
            wiggle.setDuration(140);
            wiggle.setRepeatCount(ValueAnimator.INFINITE);
            wiggle.setRepeatMode(ValueAnimator.REVERSE);
            wiggle.start();
        }
    }
}
