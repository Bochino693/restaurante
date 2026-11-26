package com.iniciorapido;

import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.ComponentName;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.os.Build;
import android.provider.Settings;

import java.io.BufferedReader;
import java.io.FileReader;
import java.util.List;

/** Configuracao e rotinas compartilhadas. Sem bibliotecas externas para o processo subir rapido. */
final class Cfg {
    static final String K_PKG = "pkg";
    static final String K_CLS = "cls";
    static final String K_LABEL = "label";
    static final String K_LEANBACK = "leanback";
    static final String K_ON = "on";
    static final String K_DELAY = "delay";
    static final String K_BOOT = "boot";
    static final String K_SCHED = "sched";
    static final String K_DIAG = "diag";
    static final String K_KIOSK = "kiosk";

    private Cfg() {}

    /** Preferencias em armazenamento que ja pode ser lido logo no inicio do boot. */
    static SharedPreferences sp(Context c) {
        if (Build.VERSION.SDK_INT >= 24) {
            try {
                c = c.createDeviceProtectedStorageContext();
            } catch (Throwable ignored) {}
        }
        return c.getSharedPreferences("cfg", Context.MODE_PRIVATE);
    }

    /** Traz a configuracao da versao 1.0, que ficava no armazenamento comum. */
    static void migrate(Context c) {
        if (Build.VERSION.SDK_INT >= 24) {
            try {
                c.createDeviceProtectedStorageContext().moveSharedPreferencesFrom(c, "cfg");
            } catch (Throwable ignored) {}
        }
    }

    /** Identificador unico da inicializacao atual, ou null se o aparelho nao informar. */
    static String bootId(Context c) {
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

    /** Abre o jogo/app escolhido. Devolve null se abriu, ou o motivo da falha. */
    static String launchTarget(Context c, SharedPreferences sp) {
        String pkg = sp.getString(K_PKG, "");
        String cls = sp.getString(K_CLS, "");
        if (pkg.length() == 0) return "nenhum jogo escolhido";
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
                return null;
            } catch (Throwable ignored) {}
        }
        try {
            PackageManager pm = c.getPackageManager();
            Intent i = pm.getLaunchIntentForPackage(pkg);
            if (i == null && Build.VERSION.SDK_INT >= 21) i = pm.getLeanbackLaunchIntentForPackage(pkg);
            if (i == null) return "o jogo nao esta mais instalado";
            i.setFlags(flags);
            c.startActivity(i);
            return null;
        } catch (Throwable t) {
            return String.valueOf(t.getMessage());
        }
    }

    static Intent homeIntent() {
        Intent i = new Intent(Intent.ACTION_MAIN);
        i.addCategory(Intent.CATEGORY_HOME);
        return i;
    }

    /** true se o Inicio Rapido e a tela inicial padrao da box. */
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

    /** Abre o launcher normal da TV box (de preferencia o que veio de fabrica). */
    static boolean launchRealHome(Context c) {
        try {
            List<ResolveInfo> all = c.getPackageManager().queryIntentActivities(homeIntent(), 0);
            ResolveInfo pick = null;
            for (int k = 0; k < all.size(); k++) {
                ResolveInfo ri = all.get(k);
                if (ri.activityInfo == null) continue;
                if (c.getPackageName().equals(ri.activityInfo.packageName)) continue;
                if (ri.activityInfo.name.endsWith("FallbackHome")) continue;
                boolean sys = (ri.activityInfo.applicationInfo.flags & ApplicationInfo.FLAG_SYSTEM) != 0;
                if (pick == null || (sys && (pick.activityInfo.applicationInfo.flags
                        & ApplicationInfo.FLAG_SYSTEM) == 0)) {
                    pick = ri;
                }
            }
            if (pick == null) return false;
            Intent i = homeIntent();
            i.setComponent(new ComponentName(pick.activityInfo.packageName, pick.activityInfo.name));
            i.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
            c.startActivity(i);
            return true;
        } catch (Throwable t) {
            return false;
        }
    }
}
