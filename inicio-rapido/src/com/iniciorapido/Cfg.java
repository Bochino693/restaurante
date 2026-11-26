package com.iniciorapido;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.os.Build;
import android.os.SystemClock;
import android.provider.Settings;

import java.io.BufferedReader;
import java.io.FileReader;
import java.util.ArrayList;
import java.util.List;

/** Configuracao e rotinas compartilhadas. Sem bibliotecas externas para o processo subir rapido. */
final class Cfg {
    static final String K_PKG = "pkg";
    static final String K_CLS = "cls";
    static final String K_LABEL = "label";
    static final String K_LEANBACK = "leanback";
    static final String K_ON = "on";
    static final String K_DELAY = "delay";
    static final String K_KIOSK = "kiosk";
    static final String K_HOME_PKG = "home_pkg";
    static final String K_HOME_CLS = "home_cls";
    private static final String K_BOOT = "boot";
    private static final String K_ELAPSED = "elapsed";

    private Cfg() {}

    static SharedPreferences sp(Context c) {
        return c.getSharedPreferences("cfg", Context.MODE_PRIVATE);
    }

    /** Identificador unico da inicializacao atual, ou null se o aparelho nao informar. */
    private static String bootId(Context c) {
        if (Build.VERSION.SDK_INT >= 24) {
            try {
                int n = Settings.Global.getInt(c.getContentResolver(), "boot_count", -1);
                if (n >= 0) return "c" + n;
            } catch (Throwable ignored) {}
        }
        BufferedReader r = null;
        try {
            r = new BufferedReader(new FileReader("/proc/sys/kernel/random/boot_id"), 64);
            String s = r.readLine();
            if (s != null && s.length() > 8) return s.trim();
        } catch (Throwable ignored) {
        } finally {
            if (r != null) try { r.close(); } catch (Throwable ignored) {}
        }
        return null;
    }

    /** true se o jogo ainda nao foi aberto desde que o aparelho ligou. */
    static boolean isNewBoot(Context c, SharedPreferences sp) {
        String id = bootId(c);
        if (id != null) return !id.equals(sp.getString(K_BOOT, ""));
        // Sem identificador: o relogio desde o boot so volta para tras quando o aparelho reinicia.
        long now = SystemClock.elapsedRealtime();
        long last = sp.getLong(K_ELAPSED, -1);
        if (last >= 0 && now >= last) {
            sp.edit().putLong(K_ELAPSED, now).apply();
            return false;
        }
        return true;
    }

    static void markBoot(Context c, SharedPreferences sp) {
        SharedPreferences.Editor e = sp.edit();
        String id = bootId(c);
        if (id != null) e.putString(K_BOOT, id);
        e.putLong(K_ELAPSED, SystemClock.elapsedRealtime());
        e.apply();
    }

    /** Abre o jogo/app escolhido. */
    static boolean launchTarget(Context c, SharedPreferences sp) {
        String pkg = sp.getString(K_PKG, "");
        String cls = sp.getString(K_CLS, "");
        if (pkg.length() == 0) return false;
        int flags = Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED;
        if (cls.length() > 0) {
            // Caminho rapido: sem consultar o PackageManager.
            try {
                Intent i = new Intent(Intent.ACTION_MAIN);
                i.addCategory(sp.getBoolean(K_LEANBACK, false)
                        ? "android.intent.category.LEANBACK_LAUNCHER"
                        : Intent.CATEGORY_LAUNCHER);
                i.setClassName(pkg, cls);
                i.setFlags(flags);
                c.startActivity(i);
                return true;
            } catch (Throwable ignored) {}
        }
        try {
            PackageManager pm = c.getPackageManager();
            Intent i = pm.getLaunchIntentForPackage(pkg);
            if (i == null && Build.VERSION.SDK_INT >= 21) i = pm.getLeanbackLaunchIntentForPackage(pkg);
            if (i == null) return false;
            i.setFlags(flags);
            c.startActivity(i);
            return true;
        } catch (Throwable t) {
            return false;
        }
    }

    static Intent homeIntent() {
        Intent i = new Intent(Intent.ACTION_MAIN);
        i.addCategory(Intent.CATEGORY_HOME);
        return i;
    }

    static boolean isDefaultHome(Context c) {
        try {
            ResolveInfo ri = c.getPackageManager()
                    .resolveActivity(homeIntent(), PackageManager.MATCH_DEFAULT_ONLY);
            return ri != null && ri.activityInfo != null
                    && c.getPackageName().equals(ri.activityInfo.packageName);
        } catch (Throwable t) {
            return false;
        }
    }

    /** Os outros launchers instalados (o launcher "de verdade" da TV box). */
    static List<ResolveInfo> otherHomes(Context c) {
        List<ResolveInfo> out = new ArrayList<ResolveInfo>();
        try {
            List<ResolveInfo> all = c.getPackageManager().queryIntentActivities(homeIntent(), 0);
            for (int k = 0; k < all.size(); k++) {
                ResolveInfo ri = all.get(k);
                if (ri.activityInfo == null) continue;
                if (c.getPackageName().equals(ri.activityInfo.packageName)) continue;
                if (ri.activityInfo.name.endsWith("FallbackHome")) continue;
                out.add(ri);
            }
        } catch (Throwable ignored) {}
        return out;
    }

    private static ComponentName realHome(Context c, SharedPreferences sp) {
        String hp = sp.getString(K_HOME_PKG, "");
        String hc = sp.getString(K_HOME_CLS, "");
        List<ResolveInfo> list = otherHomes(c);
        ResolveInfo pick = null;
        for (int k = 0; k < list.size(); k++) {
            ResolveInfo ri = list.get(k);
            if (hp.equals(ri.activityInfo.packageName) && hc.equals(ri.activityInfo.name)) {
                pick = ri;
                break;
            }
            boolean sys = (ri.activityInfo.applicationInfo.flags & ApplicationInfo.FLAG_SYSTEM) != 0;
            if (pick == null || (sys && (pick.activityInfo.applicationInfo.flags
                    & ApplicationInfo.FLAG_SYSTEM) == 0)) {
                pick = ri;
            }
        }
        if (pick == null) return null;
        return new ComponentName(pick.activityInfo.packageName, pick.activityInfo.name);
    }

    /** Abre o launcher normal da TV box. */
    static boolean launchRealHome(Context c, SharedPreferences sp) {
        ComponentName cn = realHome(c, sp);
        if (cn == null) return false;
        try {
            Intent i = homeIntent();
            i.setComponent(cn);
            i.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
            c.startActivity(i);
            return true;
        } catch (Throwable t) {
            return false;
        }
    }
}
