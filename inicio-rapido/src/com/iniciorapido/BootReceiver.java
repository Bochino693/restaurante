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
 * Modo compativel (quando o app nao e o launcher padrao): abre o jogo ao
 * receber o BOOT_COMPLETED. Com atraso 0 abre na hora, sem agendar alarme.
 */
public class BootReceiver extends BroadcastReceiver {
    static final String ACTION_LAUNCH = "com.iniciorapido.LAUNCH";

    @Override
    public void onReceive(Context c, Intent intent) {
        SharedPreferences sp = Cfg.sp(c);
        if (!sp.getBoolean(Cfg.K_ON, true) || sp.getString(Cfg.K_PKG, "").length() == 0) return;
        if (!Cfg.isNewBoot(c, sp)) return; // o modo rapido ja abriu o jogo

        boolean alarm = ACTION_LAUNCH.equals(intent.getAction());
        if (!alarm && Cfg.isDefaultHome(c)) return; // o HomeActivity cuida disso

        int delay = alarm ? 0 : sp.getInt(Cfg.K_DELAY, 0);
        if (delay <= 0) {
            Cfg.markBoot(c, sp);
            Cfg.launchTarget(c, sp);
            return;
        }
        Intent i = new Intent(c, BootReceiver.class).setAction(ACTION_LAUNCH);
        PendingIntent pi = PendingIntent.getBroadcast(c, 0, i,
                Build.VERSION.SDK_INT >= 23 ? 0x04000000 /* FLAG_IMMUTABLE */ : 0);
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
