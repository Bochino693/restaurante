package com.iniciorapido;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.os.SystemClock;

/**
 * Modo tela inicial. Quando este app e a tela inicial padrao, o Android abre
 * esta tela no instante em que termina de ligar, e ela abre o jogo na hora.
 * Como aqui o app esta em primeiro plano, o Android 10+ nao bloqueia nada.
 * Depois disso o botao Home leva ao launcher normal (ou de volta ao jogo, no
 * modo "preso no jogo").
 */
public class HomeActivity extends Activity {
    private static final int ESCAPE_PRESSES = 5;
    private static final long ESCAPE_WINDOW_MS = 4000;
    private static final long[] presses = new long[ESCAPE_PRESSES];
    private static int pressIdx;
    private static boolean launchedInProcess;

    private SharedPreferences sp;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        sp = Cfg.sp(this);
        boolean ready = sp.getBoolean(Cfg.K_ON, true) && sp.getString(Cfg.K_PKG, "").length() > 0;
        String id = Cfg.bootId(this);
        boolean newBoot = id != null ? !id.equals(sp.getString(Cfg.K_BOOT, "")) : !launchedInProcess;

        if (ready && newBoot) {
            launchedInProcess = true;
            String err = Cfg.launchTarget(this, sp);
            SharedPreferences.Editor e = sp.edit();
            if (id != null) e.putString(Cfg.K_BOOT, id);
            e.putString(Cfg.K_DIAG, (err == null ? "Abriu o jogo " : "Tentou abrir o jogo ")
                    + (SystemClock.elapsedRealtime() / 1000) + " s depois de ligar (modo tela "
                    + "inicial)" + (err == null ? "" : " - falhou: " + err));
            e.apply();
            if (err == null) finish(); else fallback();
            return;
        }
        if (ready && sp.getBoolean(Cfg.K_KIOSK, false)) {
            if (escapePressed()) {
                openSettings();
            } else if (Cfg.launchTarget(this, sp) == null) {
                finish();
            } else {
                fallback();
            }
            return;
        }
        fallback();
    }

    /** No modo "preso no jogo", 5 toques seguidos no Home abrem a configuracao. */
    private static boolean escapePressed() {
        long now = SystemClock.elapsedRealtime();
        presses[pressIdx] = now;
        pressIdx = (pressIdx + 1) % ESCAPE_PRESSES;
        long oldest = presses[pressIdx];
        return oldest != 0 && now - oldest <= ESCAPE_WINDOW_MS;
    }

    private void fallback() {
        if (Cfg.launchRealHome(this)) {
            finish();
        } else {
            openSettings();
        }
    }

    private void openSettings() {
        startActivity(new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        finish();
    }
}
