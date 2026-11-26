package com.iniciorapido;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.os.Handler;
import android.os.SystemClock;
import android.view.Gravity;
import android.view.KeyEvent;
import android.widget.TextView;

/**
 * Modo rapido. Quando este app e o launcher padrao, o Android abre esta tela
 * no instante em que termina de subir (bem antes do BOOT_COMPLETED) e ela
 * abre o jogo direto. Depois disso o botao Home leva ao launcher normal.
 */
public class HomeActivity extends Activity {
    private static final int ESCAPE_PRESSES = 5;
    private static final long ESCAPE_WINDOW_MS = 4000;
    private static final long[] presses = new long[ESCAPE_PRESSES];
    private static int pressIdx;

    private final Handler handler = new Handler();
    private SharedPreferences sp;
    private TextView tv;
    private int left;
    private boolean done;

    private final Runnable tick = new Runnable() {
        public void run() {
            if (done) return;
            if (left <= 0) {
                launch();
                return;
            }
            tv.setText("Iniciando " + sp.getString(Cfg.K_LABEL, "o jogo") + " em " + left
                    + " s...\n\nAperte VOLTAR para cancelar");
            left--;
            handler.postDelayed(this, 1000);
        }
    };

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        sp = Cfg.sp(this);
        boolean ready = sp.getBoolean(Cfg.K_ON, true) && sp.getString(Cfg.K_PKG, "").length() > 0;
        boolean newBoot = Cfg.isNewBoot(this, sp);
        if (newBoot) Cfg.markBoot(this, sp);

        if (ready && newBoot) {
            left = sp.getInt(Cfg.K_DELAY, 0);
            if (left <= 0) {
                launch();
            } else {
                tv = new TextView(this);
                tv.setGravity(Gravity.CENTER);
                tv.setTextSize(24);
                tv.setTextColor(0xFFFFFFFF);
                setContentView(tv);
                tick.run();
            }
            return;
        }
        if (ready && sp.getBoolean(Cfg.K_KIOSK, false)) {
            if (escapePressed()) {
                openSettings();
            } else {
                launch();
            }
            return;
        }
        fallback();
    }

    /** No modo quiosque, 5 toques seguidos no Home abrem a configuracao. */
    private static boolean escapePressed() {
        long now = SystemClock.elapsedRealtime();
        presses[pressIdx] = now;
        pressIdx = (pressIdx + 1) % ESCAPE_PRESSES;
        long oldest = presses[pressIdx];
        return oldest != 0 && now - oldest <= ESCAPE_WINDOW_MS;
    }

    private void launch() {
        done = true;
        if (Cfg.launchTarget(this, sp)) {
            finish();
        } else {
            fallback();
        }
    }

    private void fallback() {
        done = true;
        if (Cfg.launchRealHome(this, sp)) {
            finish();
        } else {
            openSettings();
        }
    }

    private void openSettings() {
        done = true;
        startActivity(new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        finish();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent e) {
        if (!done && keyCode == KeyEvent.KEYCODE_BACK) {
            handler.removeCallbacks(tick);
            fallback();
            return true;
        }
        return super.onKeyDown(keyCode, e);
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacks(tick);
        super.onDestroy();
    }
}
