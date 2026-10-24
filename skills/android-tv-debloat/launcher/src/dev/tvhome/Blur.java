package dev.tvhome;

import android.graphics.Bitmap;

/** A cheap blur: shrink the photo to a few dozen pixels, box-blur it, let the GPU stretch it back. */
final class Blur {
    private static final int WIDTH = 64;

    private Blur() {}

    static Bitmap backdrop(Bitmap src) {
        int w = WIDTH;
        int h = Math.max(1, Math.round(WIDTH * src.getHeight() / (float) src.getWidth()));
        Bitmap small = Bitmap.createScaledBitmap(src, w, h, true);
        int[] px = new int[w * h];
        small.getPixels(px, 0, w, 0, 0, w, h);
        if (small != src) {
            small.recycle();
        }
        int[] tmp = new int[w * h];
        for (int pass = 0; pass < 3; pass++) {
            box(px, tmp, w, h, 2, true);
            box(tmp, px, w, h, 2, false);
        }
        return Bitmap.createBitmap(px, w, h, Bitmap.Config.ARGB_8888);
    }

    private static void box(int[] in, int[] out, int w, int h, int r, boolean horizontal) {
        int lines = horizontal ? h : w;
        int len = horizontal ? w : h;
        for (int line = 0; line < lines; line++) {
            for (int i = 0; i < len; i++) {
                int rs = 0;
                int gs = 0;
                int bs = 0;
                int n = 0;
                for (int k = -r; k <= r; k++) {
                    int j = Math.max(0, Math.min(len - 1, i + k));
                    int p = horizontal ? in[line * w + j] : in[j * w + line];
                    rs += (p >> 16) & 0xFF;
                    gs += (p >> 8) & 0xFF;
                    bs += p & 0xFF;
                    n++;
                }
                int v = 0xFF000000 | ((rs / n) << 16) | ((gs / n) << 8) | (bs / n);
                if (horizontal) {
                    out[line * w + i] = v;
                } else {
                    out[i * w + line] = v;
                }
            }
        }
    }
}
