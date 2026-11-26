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
 * Abre o jogo depois que a TV box liga.
 *
 * No Android 10+ um app em segundo plano nao pode abrir outro direto de um
 * aviso de boot; so pode quando e acordado por um alarme do sistema. E por
 * isso que o AutoStart original (boot -> alarme -> abre) funciona nessas boxes.
 * Aqui o caminho e o mesmo, mas o alarme e armado no PRIMEIRO aviso que o
 * Android entregar (boot bloqueado, armazenamento, rede, boot completo com
 * prioridade alta), em vez de esperar a fila inteira do BOOT_COMPLETED.
 * Em Android antigo (ate o 9) o jogo abre direto, sem alarme.
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
        boolean boot = quick || action.endsWith("BOOT_COMPLETED");
        String id = Cfg.bootId(c);
        long up = SystemClock.elapsedRealtime();
        String via = action.substring(action.lastIndexOf('.') + 1);

        if (alarm) {
            // Acordado pelo alarme: aqui o Android deixa abrir o jogo.
            if (id != null && id.equals(sp.getString(Cfg.K_BOOT, ""))) return; // alarme de reforco
            launch(c, sp, id, intent.getStringExtra("via"), up);
            return;
        }
        if (!quick) {
            if (id != null && id.equals(sp.getString(Cfg.K_BOOT, ""))) return;  // ja abriu
            if (id != null && id.equals(sp.getString(Cfg.K_SCHED, ""))) return; // ja armado
            if (!boot && (id == null || up > EARLY_WINDOW_MS)) return;
        }

        int delay = sp.getInt(Cfg.K_DELAY, 0);
        boolean needAlarm = Build.VERSION.SDK_INT >= 29 || delay > 0;
        if (needAlarm) {
            SharedPreferences.Editor e = sp.edit();
            if (quick) e.remove(Cfg.K_BOOT); // religou sem reiniciar: vale como novo boot
            if (id != null) e.putString(Cfg.K_SCHED, id);
            e.apply();
            schedule(c, delay, via);
            // Tentativa direta: de graca, e ganha uns segundos se esta box permitir.
            if (delay == 0) Cfg.launchTarget(c, sp);
        } else {
            launch(c, sp, id, via, up);
        }
    }

    private static void launch(Context c, SharedPreferences sp, String id, String via, long up) {
        String err = Cfg.launchTarget(c, sp);
        SharedPreferences.Editor e = sp.edit();
        if (id != null) e.putString(Cfg.K_BOOT, id);
        e.putString(Cfg.K_DIAG, (err == null ? "Abriu o jogo " : "Tentou abrir o jogo ")
                + (up / 1000) + " s depois de ligar"
                + (via == null ? "" : " (aviso " + via + ")")
                + (err == null ? "" : " - falhou: " + err));
        e.apply();
    }

    private static void schedule(Context c, int delay, String via) {
        int immutable = Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0;
        Intent i = new Intent(c, BootReceiver.class).setAction(ACTION_LAUNCH).putExtra("via", via);
        PendingIntent pi = PendingIntent.getBroadcast(c, 0, i,
                PendingIntent.FLAG_UPDATE_CURRENT | immutable);
        AlarmManager am = (AlarmManager) c.getSystemService(Context.ALARM_SERVICE);
        long ms = delay * 1000L + 100; // o Android arredonda para o minimo que ele aceita
        long at = SystemClock.elapsedRealtime() + ms;
        try {
            if (Build.VERSION.SDK_INT >= 23) {
                am.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi);
            } else if (Build.VERSION.SDK_INT >= 19) {
                am.setExact(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi);
            } else {
                am.set(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, pi);
            }
        } catch (Throwable ignored) {}
        if (Build.VERSION.SDK_INT >= 29) {
            // Reforco: alarme do tipo "despertador", que o Android trata com prioridade maxima.
            try {
                Intent j = new Intent(c, BootReceiver.class).setAction(ACTION_LAUNCH)
                        .putExtra("via", via);
                PendingIntent pj = PendingIntent.getBroadcast(c, 1, j,
                        PendingIntent.FLAG_UPDATE_CURRENT | immutable);
                PendingIntent show = PendingIntent.getActivity(c, 2,
                        new Intent(c, MainActivity.class), immutable);
                am.setAlarmClock(new AlarmManager.AlarmClockInfo(
                        System.currentTimeMillis() + ms, show), pj);
            } catch (Throwable ignored) {}
        }
    }
}
