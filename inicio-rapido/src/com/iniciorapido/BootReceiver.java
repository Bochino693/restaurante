package com.iniciorapido;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.SystemClock;

/**
 * Abre o jogo no primeiro aviso que o Android entregar depois de ligar.
 *
 * O AutoStart original so ouvia o BOOT_COMPLETED (que numa box fraca passa por
 * dezenas de apps antes) e ainda agendava um alarme. Aqui ouvimos tambem avisos
 * que chegam antes (LOCKED_BOOT_COMPLETED, armazenamento montado, rede) e, com
 * atraso 0, o jogo abre na mesma hora.
 */
public class BootReceiver extends BroadcastReceiver {
    static final String ACTION_LAUNCH = "com.iniciorapido.LAUNCH";
    /** Avisos de rede/armazenamento so contam como "boot" neste intervalo apos ligar. */
    private static final long EARLY_WINDOW_MS = 180000;

    @Override
    public void onReceive(Context c, Intent intent) {
        String action = intent.getAction();
        if (action == null) return;
        SharedPreferences sp = Cfg.sp(c);
        if (!sp.getBoolean(Cfg.K_ON, true) || sp.getString(Cfg.K_PKG, "").length() == 0) return;

        boolean alarm = ACTION_LAUNCH.equals(action);
        boolean quick = action.endsWith("QUICKBOOT_POWERON");
        boolean boot = alarm || quick || action.endsWith("BOOT_COMPLETED");
        String id = Cfg.bootId(c);
        long up = SystemClock.elapsedRealtime();

        if (!quick) {
            if (id != null && id.equals(sp.getString(Cfg.K_BOOT, ""))) return; // ja abriu neste boot
            if (!boot && (id == null || up > EARLY_WINDOW_MS)) return;
        }

        int delay = alarm ? 0 : sp.getInt(Cfg.K_DELAY, 0);
        if (delay > 0) {
            if (id != null && id.equals(sp.getString(Cfg.K_SCHED, ""))) return; // ja agendado
            if (id != null) sp.edit().putString(Cfg.K_SCHED, id).apply();
            schedule(c, delay);
            return;
        }

        String err = Cfg.launchTarget(c, sp);
        String via = action.substring(action.lastIndexOf('.') + 1);
        SharedPreferences.Editor e = sp.edit();
        if (id != null) e.putString(Cfg.K_BOOT, id);
        e.putString(Cfg.K_DIAG, (err == null ? "Mandou abrir o jogo " : "Tentou abrir o jogo ")
                + (up / 1000) + " s depois de ligar (aviso " + via + ")"
                + (err == null ? "" : " - falhou: " + err));
        e.apply();
    }

    private static void schedule(Context c, int delay) {
        Intent i = new Intent(c, BootReceiver.class).setAction(ACTION_LAUNCH);
        PendingIntent pi = PendingIntent.getBroadcast(c, 0, i,
                Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0);
        AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
        long at = SystemClock.elapsedRealtime() + delay * 1000L;
        if (Build.VERSION.SDK_INT >= 23) {
            am.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi);
        } else if (Build.VERSION.SDK_INT >= 19) {
            am.setExact(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi);
        } else {
            am.set(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi);
        }
    }
}
