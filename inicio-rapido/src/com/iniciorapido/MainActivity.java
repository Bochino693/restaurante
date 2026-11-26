package com.iniciorapido;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.ProgressDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;

/** Tela de configuracao, toda navegavel pelo controle remoto (setas + OK). */
public class MainActivity extends Activity {
    private SharedPreferences sp;
    private TextView status;
    private Button bApp, bOn, bMinus, bPlus, bMode, bHome, bKiosk, bOverlay, bTest;
    private TextView tDelay;

    private static final class App {
        String label, pkg, cls;
        boolean leanback;
    }

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        sp = Cfg.sp(this);
        // Quem abriu a configuracao nao quer que o proximo Home dispare o jogo.
        Cfg.markBoot(this, sp);

        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(pad * 2, pad, pad * 2, pad);

        status = new TextView(this);
        status.setTextSize(16);
        status.setPadding(0, 0, 0, pad);
        root.addView(status);

        bApp = button(root, new View.OnClickListener() {
            public void onClick(View v) { pickApp(); }
        });
        bOn = button(root, new View.OnClickListener() {
            public void onClick(View v) { toggle(Cfg.K_ON, true); }
        });

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        bMinus = new Button(this);
        bMinus.setText("  -  ");
        bMinus.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) { delay(-1); }
        });
        tDelay = new TextView(this);
        tDelay.setTextSize(18);
        tDelay.setPadding(pad, 0, pad, 0);
        bPlus = new Button(this);
        bPlus.setText("  +  ");
        bPlus.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) { delay(1); }
        });
        row.addView(bMinus);
        row.addView(tDelay);
        row.addView(bPlus);
        root.addView(row);

        bMode = button(root, new View.OnClickListener() {
            public void onClick(View v) { chooseDefaultHome(); }
        });
        bHome = button(root, new View.OnClickListener() {
            public void onClick(View v) { pickRealHome(); }
        });
        bKiosk = button(root, new View.OnClickListener() {
            public void onClick(View v) { toggle(Cfg.K_KIOSK, false); }
        });
        bOverlay = button(root, new View.OnClickListener() {
            public void onClick(View v) { askOverlay(); }
        });
        bTest = button(root, new View.OnClickListener() {
            public void onClick(View v) {
                if (!Cfg.launchTarget(MainActivity.this, sp)) toast("Escolha um jogo primeiro");
            }
        });
        bTest.setText("Testar agora (abrir o jogo)");

        TextView help = new TextView(this);
        help.setPadding(0, pad, 0, 0);
        help.setText("MODO RAPIDO: deixe o Inicio Rapido como launcher padrao. O jogo abre assim que "
                + "o Android termina de subir, sem esperar o resto do sistema. Depois disso o botao "
                + "Home leva ao launcher normal.\n\n"
                + "MODO COMPATIVEL: sem trocar o launcher, o jogo abre quando o Android avisa que "
                + "terminou o boot (mais lento, igual ao app antigo).\n\n"
                + "Modo quiosque: o Home sempre volta para o jogo. Para abrir esta tela, aperte "
                + "Home 5 vezes seguidas.");
        root.addView(help);

        ScrollView sv = new ScrollView(this);
        sv.addView(root);
        setContentView(sv);
        bApp.requestFocus();
    }

    private Button button(LinearLayout parent, View.OnClickListener l) {
        Button x = new Button(this);
        x.setOnClickListener(l);
        parent.addView(x, new LinearLayout.LayoutParams(-1, -2));
        return x;
    }

    @Override
    protected void onResume() {
        super.onResume();
        refresh();
    }

    private void refresh() {
        String label = sp.getString(Cfg.K_LABEL, "");
        boolean has = sp.getString(Cfg.K_PKG, "").length() > 0;
        boolean isHome = Cfg.isDefaultHome(this);
        boolean on = sp.getBoolean(Cfg.K_ON, true);

        bApp.setText(has ? "Jogo: " + label + "  (trocar)" : "Escolher o jogo / app");
        bOn.setText("Iniciar ao ligar: " + (on ? "SIM" : "NAO"));
        tDelay.setText("Atraso: " + sp.getInt(Cfg.K_DELAY, 0) + " s");
        bMode.setText(isHome ? "Modo rapido ATIVO  (trocar launcher padrao)"
                : "Ativar modo rapido (definir como launcher padrao)");
        bKiosk.setText("Modo quiosque: " + (sp.getBoolean(Cfg.K_KIOSK, false) ? "SIM" : "NAO"));

        List<ResolveInfo> homes = Cfg.otherHomes(this);
        bHome.setVisibility(homes.size() > 1 ? View.VISIBLE : View.GONE);
        String hp = sp.getString(Cfg.K_HOME_PKG, "");
        bHome.setText("Launcher normal: " + (hp.length() > 0 ? hp : "automatico") + "  (trocar)");

        boolean needOverlay = Build.VERSION.SDK_INT >= 29 && !isHome && !canOverlay();
        bOverlay.setVisibility(needOverlay ? View.VISIBLE : View.GONE);
        bOverlay.setText("Permitir abrir sobre outros apps (necessario no Android 10+)");

        String s;
        if (!has) s = "Escolha o jogo que deve abrir quando a TV box ligar.";
        else if (!on) s = "Inicio automatico desligado.";
        else if (isHome) s = "Pronto: " + label + " abre no modo RAPIDO ao ligar.";
        else if (needOverlay) s = "Atencao: neste Android o modo compativel precisa da permissao "
                + "abaixo. Prefira o modo rapido.";
        else s = "Pronto: " + label + " abre no modo compativel. Ative o modo rapido para iniciar "
                + "mais cedo.";
        status.setText(s);
    }

    private void toggle(String key, boolean def) {
        sp.edit().putBoolean(key, !sp.getBoolean(key, def)).apply();
        refresh();
    }

    private void delay(int d) {
        int v = Math.max(0, Math.min(120, sp.getInt(Cfg.K_DELAY, 0) + d));
        sp.edit().putInt(Cfg.K_DELAY, v).apply();
        refresh();
    }

    private void toast(String s) {
        Toast.makeText(this, s, Toast.LENGTH_LONG).show();
    }

    private boolean canOverlay() {
        return Build.VERSION.SDK_INT < 23 || Settings.canDrawOverlays(this);
    }

    private void askOverlay() {
        try {
            startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName())));
        } catch (Throwable t) {
            toast("Esta TV box nao tem essa tela. Use o modo rapido.");
        }
    }

    /** Lista os apps numa thread: a consulta e lenta em aparelhos com 1 GB. */
    private void pickApp() {
        final ProgressDialog pd = ProgressDialog.show(this, null, "Carregando apps...", true, true);
        new Thread(new Runnable() {
            public void run() {
                final List<App> apps = loadApps();
                runOnUiThread(new Runnable() {
                    public void run() {
                        if (isFinishing()) return;
                        try { pd.dismiss(); } catch (Throwable ignored) {}
                        showApps(apps);
                    }
                });
            }
        }).start();
    }

    private List<App> loadApps() {
        PackageManager pm = getPackageManager();
        List<App> out = new ArrayList<App>();
        HashSet<String> seen = new HashSet<String>();
        String[] cats = {Intent.CATEGORY_LAUNCHER, "android.intent.category.LEANBACK_LAUNCHER"};
        for (int c = 0; c < cats.length; c++) {
            Intent q = new Intent(Intent.ACTION_MAIN);
            q.addCategory(cats[c]);
            List<ResolveInfo> l;
            try {
                l = pm.queryIntentActivities(q, 0);
            } catch (Throwable t) {
                continue;
            }
            for (int k = 0; k < l.size(); k++) {
                ResolveInfo ri = l.get(k);
                if (ri.activityInfo == null) continue;
                String pkg = ri.activityInfo.packageName;
                if (pkg.equals(getPackageName()) || !seen.add(pkg)) continue;
                App a = new App();
                a.pkg = pkg;
                a.cls = ri.activityInfo.name;
                a.leanback = c == 1;
                CharSequence lb = null;
                try { lb = ri.loadLabel(pm); } catch (Throwable ignored) {}
                a.label = lb == null ? pkg : lb.toString();
                out.add(a);
            }
        }
        Collections.sort(out, new Comparator<App>() {
            public int compare(App a, App b) { return a.label.compareToIgnoreCase(b.label); }
        });
        return out;
    }

    private void showApps(final List<App> apps) {
        String[] names = new String[apps.size()];
        for (int k = 0; k < names.length; k++) names[k] = apps.get(k).label + "\n" + apps.get(k).pkg;
        new AlertDialog.Builder(this).setTitle("Qual jogo abrir ao ligar?")
                .setItems(names, new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface d, int w) {
                        App a = apps.get(w);
                        sp.edit().putString(Cfg.K_PKG, a.pkg).putString(Cfg.K_CLS, a.cls)
                                .putString(Cfg.K_LABEL, a.label)
                                .putBoolean(Cfg.K_LEANBACK, a.leanback)
                                .putBoolean(Cfg.K_ON, true).apply();
                        refresh();
                    }
                }).setNegativeButton("Cancelar", null).show();
    }

    private void pickRealHome() {
        final List<ResolveInfo> homes = Cfg.otherHomes(this);
        String[] names = new String[homes.size() + 1];
        names[0] = "Automatico";
        for (int k = 0; k < homes.size(); k++) {
            CharSequence lb = null;
            try { lb = homes.get(k).loadLabel(getPackageManager()); } catch (Throwable ignored) {}
            names[k + 1] = (lb == null ? "" : lb + "\n") + homes.get(k).activityInfo.packageName;
        }
        new AlertDialog.Builder(this).setTitle("Launcher normal (botao Home)")
                .setItems(names, new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface d, int w) {
                        SharedPreferences.Editor e = sp.edit();
                        if (w == 0) {
                            e.remove(Cfg.K_HOME_PKG).remove(Cfg.K_HOME_CLS);
                        } else {
                            e.putString(Cfg.K_HOME_PKG, homes.get(w - 1).activityInfo.packageName);
                            e.putString(Cfg.K_HOME_CLS, homes.get(w - 1).activityInfo.name);
                        }
                        e.apply();
                        refresh();
                    }
                }).show();
    }

    /** Abre a tela do Android para escolher o launcher padrao. */
    private void chooseDefaultHome() {
        toast("Escolha \"Inicio Rapido\" e marque SEMPRE");
        if (Build.VERSION.SDK_INT >= 21) {
            try {
                startActivity(new Intent(Settings.ACTION_HOME_SETTINGS));
                return;
            } catch (Throwable ignored) {}
        }
        try {
            // Sem tela de configuracao: o proprio Home mostra a pergunta "abrir com".
            startActivity(Cfg.homeIntent().addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        } catch (Throwable t) {
            toast("Abra Configuracoes > Apps > Tela inicial e escolha Inicio Rapido");
        }
    }
}
