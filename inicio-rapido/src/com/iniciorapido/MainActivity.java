package com.iniciorapido;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.ProgressDialog;
import android.content.Context;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.content.res.ColorStateList;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.StateListDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;

/** Tela de configuracao, toda navegavel pelo controle remoto (setas + OK). */
public class MainActivity extends Activity {
    private static final int BG_TOP = 0xFF16213A, BG_BOTTOM = 0xFF090C14, CARD = 0xFF1C2740;
    private static final int ACCENT = 0xFFFFB020, GREEN = 0xFF2ECC71, RED = 0xFFFF5A4F;
    private static final int TEXT = 0xFFFFFFFF, SUB = 0xFF9FB0CF, BTN = 0xFF2B3A5C, DARK = 0xFF10141F;

    private SharedPreferences sp;
    private float dp;
    private final List<View> cards = new ArrayList<View>();

    private TextView pill, gameName, gamePkg, bPick, bTest, bOn, tDelay, tDiag;
    private ImageView gameIcon;
    private TextView dotBoot, dotOverlay, dotBattery, subOverlay, bOverlay, bBattery;

    private static final class App {
        String label, pkg, cls;
        boolean leanback;
    }

    // ---------------------------------------------------------------- tela

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        Cfg.migrate(this);
        sp = Cfg.sp(this);
        dp = getResources().getDisplayMetrics().density;
        // Quem esta configurando nao quer o jogo abrindo por cima no meio do caminho.
        String id = Cfg.bootId(this);
        if (id != null) sp.edit().putString(Cfg.K_BOOT, id).apply();

        LinearLayout root = col();
        root.setPadding(px(28), px(20), px(28), px(20));

        // Cabecalho
        LinearLayout head = row();
        head.setGravity(Gravity.CENTER_VERTICAL);
        ImageView logo = new ImageView(this);
        logo.setImageResource(R.drawable.icon);
        head.addView(logo, new LinearLayout.LayoutParams(px(52), px(52)));
        LinearLayout titles = col();
        titles.setPadding(px(14), 0, 0, 0);
        titles.addView(text("Início Rápido", 26, TEXT, true));
        titles.addView(text("Abre o seu jogo sozinho assim que a TV box liga", 14, SUB, false));
        head.addView(titles, new LinearLayout.LayoutParams(0, -2, 1));
        pill = text("", 14, DARK, true);
        pill.setPadding(px(16), px(8), px(16), px(8));
        head.addView(pill);
        root.addView(head);

        // Em tela larga (TV) usa duas colunas; em tela estreita, uma so.
        boolean wide = getResources().getDisplayMetrics().widthPixels / dp >= 700;
        LinearLayout left = col(), right = wide ? col() : left;
        if (wide) {
            LinearLayout cols = row();
            LinearLayout.LayoutParams l = new LinearLayout.LayoutParams(0, -2, 1);
            l.rightMargin = px(8);
            LinearLayout.LayoutParams r = new LinearLayout.LayoutParams(0, -2, 1);
            r.leftMargin = px(8);
            cols.addView(left, l);
            cols.addView(right, r);
            root.addView(cols, margin(-1, -2, 0, px(18), 0, 0));
        } else {
            root.addView(left, margin(-1, -2, 0, px(18), 0, 0));
        }

        // Cartao: jogo
        LinearLayout cGame = card(left, "JOGO");
        LinearLayout g = row();
        g.setGravity(Gravity.CENTER_VERTICAL);
        gameIcon = new ImageView(this);
        g.addView(gameIcon, new LinearLayout.LayoutParams(px(56), px(56)));
        LinearLayout gt = col();
        gt.setPadding(px(14), 0, 0, 0);
        gameName = text("", 20, TEXT, true);
        gamePkg = text("", 12, SUB, false);
        gt.addView(gameName);
        gt.addView(gamePkg);
        g.addView(gt, new LinearLayout.LayoutParams(0, -2, 1));
        cGame.addView(g);
        LinearLayout gb = row();
        bPick = button("Escolher jogo", true, new View.OnClickListener() {
            public void onClick(View v) { pickApp(); }
        });
        bTest = button("▶  Testar agora", false, new View.OnClickListener() {
            public void onClick(View v) {
                String err = Cfg.launchTarget(MainActivity.this, sp);
                if (err != null) toast("Não abriu: " + err);
            }
        });
        gb.addView(bPick, margin(0, -2, 0, 0, px(6), 0, 1));
        gb.addView(bTest, margin(0, -2, px(6), 0, 0, 0, 1));
        cGame.addView(gb, margin(-1, -2, 0, px(14), 0, 0));

        // Cartao: inicio automatico
        LinearLayout cAuto = card(left, "AO LIGAR A TV BOX");
        bOn = button("", false, new View.OnClickListener() {
            public void onClick(View v) {
                sp.edit().putBoolean(Cfg.K_ON, !sp.getBoolean(Cfg.K_ON, true)).apply();
                refresh();
            }
        });
        cAuto.addView(bOn, new LinearLayout.LayoutParams(-1, -2));
        LinearLayout d = row();
        d.setGravity(Gravity.CENTER_VERTICAL);
        tDelay = text("", 16, TEXT, false);
        d.addView(tDelay, new LinearLayout.LayoutParams(0, -2, 1));
        d.addView(button("  –  ", false, new View.OnClickListener() {
            public void onClick(View v) { delay(-1); }
        }), margin(-2, -2, 0, 0, px(8), 0));
        d.addView(button("  +  ", false, new View.OnClickListener() {
            public void onClick(View v) { delay(1); }
        }));
        cAuto.addView(d, margin(-1, -2, 0, px(12), 0, 0));
        cAuto.addView(text("0 s é o mais rápido. Aumente só se o jogo abrir antes da rede ou do "
                + "armazenamento ficarem prontos.", 12, SUB, false), margin(-1, -2, 0, px(8), 0, 0));

        // Cartao: permissoes
        LinearLayout cPerm = card(right, "PERMISSÕES");
        dotBoot = dot();
        permRow(cPerm, dotBoot, "Iniciar junto com o sistema",
                text("Concedida na instalação", 12, SUB, false), null);
        dotOverlay = dot();
        subOverlay = text("", 12, SUB, false);
        bOverlay = button("Permitir", true, new View.OnClickListener() {
            public void onClick(View v) { askOverlay(); }
        });
        permRow(cPerm, dotOverlay, "Abrir sobre outros apps", subOverlay, bOverlay);
        dotBattery = dot();
        bBattery = button("Permitir", true, new View.OnClickListener() {
            public void onClick(View v) { askBattery(); }
        });
        permRow(cPerm, dotBattery, "Sem restrição de bateria",
                text("Impede o Android de segurar o app no boot", 12, SUB, false), bBattery);
        cPerm.addView(button("Abrir informações do app", false, new View.OnClickListener() {
            public void onClick(View v) { appInfo(); }
        }), margin(-1, -2, 0, px(12), 0, 0));
        cPerm.addView(text("Se a sua TV box tiver uma lista de \"inicialização automática\" ou "
                + "\"apps protegidos\", libere o Início Rápido nela também.", 12, SUB, false),
                margin(-1, -2, 0, px(8), 0, 0));

        // Cartao: diagnostico
        LinearLayout cDiag = card(right, "ÚLTIMA INICIALIZAÇÃO");
        tDiag = text("", 14, TEXT, false);
        cDiag.addView(tDiag);

        ScrollView sv = new ScrollView(this);
        sv.setFillViewport(true);
        sv.setBackgroundDrawable(new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[] {BG_TOP, BG_BOTTOM}));
        sv.addView(root);
        setContentView(sv);
        bPick.requestFocus();

        // Entrada animada dos cartoes
        for (int k = 0; k < cards.size(); k++) {
            View v = cards.get(k);
            v.setAlpha(0f);
            v.setTranslationY(px(24));
            v.animate().alpha(1f).translationY(0f).setStartDelay(80 * k).setDuration(260).start();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        refresh();
    }

    private void refresh() {
        String pkg = sp.getString(Cfg.K_PKG, "");
        boolean has = pkg.length() > 0;
        boolean on = sp.getBoolean(Cfg.K_ON, true);
        boolean overlay = Build.VERSION.SDK_INT < 23 || Settings.canDrawOverlays(this);
        boolean overlayNeeded = Build.VERSION.SDK_INT >= 29;
        boolean battery = true;
        if (Build.VERSION.SDK_INT >= 23) {
            try {
                battery = ((PowerManager) getSystemService(Context.POWER_SERVICE))
                        .isIgnoringBatteryOptimizations(getPackageName());
            } catch (Throwable ignored) {}
        }

        gameName.setText(has ? sp.getString(Cfg.K_LABEL, pkg) : "Nenhum jogo escolhido");
        gamePkg.setText(has ? pkg : "Toque em \"Escolher jogo\" para começar");
        Drawable ic = null;
        if (has) try { ic = getPackageManager().getApplicationIcon(pkg); } catch (Throwable ignored) {}
        if (ic != null) gameIcon.setImageDrawable(ic); else gameIcon.setImageResource(R.drawable.icon);
        bPick.setText(has ? "Trocar jogo" : "Escolher jogo");
        bTest.setEnabled(has);
        bTest.setAlpha(has ? 1f : 0.4f);

        bOn.setText(on ? "●  INÍCIO AUTOMÁTICO LIGADO" : "○  INÍCIO AUTOMÁTICO DESLIGADO");
        bOn.setTextColor(states(on ? GREEN : SUB));
        int s = sp.getInt(Cfg.K_DELAY, 0);
        tDelay.setText(s == 0 ? "Atraso: nenhum (imediato)" : "Atraso: " + s + " s");

        dotBoot.setTextColor(GREEN);
        dotOverlay.setTextColor(overlay ? GREEN : (overlayNeeded ? RED : ACCENT));
        subOverlay.setText(overlayNeeded ? "Obrigatória neste Android para abrir o jogo no boot"
                : "Não é exigida neste Android (" + Build.VERSION.RELEASE + ")");
        bOverlay.setVisibility(overlay ? View.GONE : View.VISIBLE);
        dotBattery.setTextColor(battery ? GREEN : ACCENT);
        bBattery.setVisibility(battery ? View.GONE : View.VISIBLE);

        String st;
        int color;
        if (!has) { st = "ESCOLHA O JOGO"; color = ACCENT; }
        else if (!on) { st = "DESLIGADO"; color = SUB; }
        else if (overlayNeeded && !overlay) { st = "FALTA PERMISSÃO"; color = RED; }
        else { st = "PRONTO"; color = GREEN; }
        pill.setText(st);
        pill.setBackgroundDrawable(shape(color, 20));

        tDiag.setText(sp.getString(Cfg.K_DIAG, "Ainda não houve uma inicialização desde a "
                + "configuração. Reinicie a TV box para testar.")
                + "\n\nAndroid " + Build.VERSION.RELEASE + " (API " + Build.VERSION.SDK_INT + ")");
    }

    private void delay(int d) {
        int v = Math.max(0, Math.min(120, sp.getInt(Cfg.K_DELAY, 0) + d));
        sp.edit().putInt(Cfg.K_DELAY, v).apply();
        refresh();
    }

    // ---------------------------------------------------------- permissoes

    private void askOverlay() {
        try {
            startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName())));
        } catch (Throwable t) {
            toast("Esta TV box não tem essa tela. Procure \"Sobrepor a outros apps\" nas "
                    + "informações do app.");
            appInfo();
        }
    }

    private void askBattery() {
        try {
            startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:" + getPackageName())));
        } catch (Throwable t) {
            try {
                startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
            } catch (Throwable t2) {
                toast("Esta TV box não tem controle de bateria; não é necessário.");
            }
        }
    }

    private void appInfo() {
        try {
            startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                    Uri.parse("package:" + getPackageName())));
        } catch (Throwable t) {
            toast("Abra Configurações > Apps > Início Rápido");
        }
    }

    // ------------------------------------------------------ escolha do jogo

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
        final HashMap<String, Drawable> icons = new HashMap<String, Drawable>();
        BaseAdapter ad = new BaseAdapter() {
            public int getCount() { return apps.size(); }
            public Object getItem(int p) { return apps.get(p); }
            public long getItemId(int p) { return p; }
            public View getView(int p, View v, ViewGroup parent) {
                LinearLayout r = (LinearLayout) v;
                if (r == null) {
                    r = row();
                    r.setGravity(Gravity.CENTER_VERTICAL);
                    r.setPadding(px(16), px(10), px(16), px(10));
                    r.addView(new ImageView(MainActivity.this),
                            new LinearLayout.LayoutParams(px(40), px(40)));
                    LinearLayout t = col();
                    t.setPadding(px(14), 0, 0, 0);
                    t.addView(text("", 17, TEXT, true));
                    t.addView(text("", 12, SUB, false));
                    r.addView(t);
                }
                App a = apps.get(p);
                Drawable d = icons.get(a.pkg);
                if (d == null) {
                    try {
                        d = getPackageManager().getApplicationIcon(a.pkg);
                        icons.put(a.pkg, d);
                    } catch (Throwable ignored) {}
                }
                ((ImageView) r.getChildAt(0)).setImageDrawable(d);
                LinearLayout t = (LinearLayout) r.getChildAt(1);
                ((TextView) t.getChildAt(0)).setText(a.label);
                ((TextView) t.getChildAt(1)).setText(a.pkg);
                return r;
            }
        };
        new AlertDialog.Builder(this, AlertDialog.THEME_DEVICE_DEFAULT_DARK)
                .setTitle("Qual jogo abrir ao ligar?")
                .setAdapter(ad, new DialogInterface.OnClickListener() {
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

    // ------------------------------------------------------- pecas visuais

    private int px(int v) { return (int) (v * dp + 0.5f); }

    private LinearLayout col() {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        return l;
    }

    private LinearLayout row() {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.HORIZONTAL);
        return l;
    }

    private TextView text(String s, int size, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(size);
        t.setTextColor(color);
        if (bold) t.setTypeface(Typeface.DEFAULT_BOLD);
        return t;
    }

    private TextView dot() {
        TextView t = text("●", 18, SUB, false);
        t.setPadding(0, 0, px(10), 0);
        return t;
    }

    private static LinearLayout.LayoutParams margin(int w, int h, int l, int t, int r, int b) {
        return margin(w, h, l, t, r, b, 0);
    }

    private static LinearLayout.LayoutParams margin(int w, int h, int l, int t, int r, int b,
            float weight) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(w, h, weight);
        p.setMargins(l, t, r, b);
        return p;
    }

    private GradientDrawable shape(int color, int radiusDp) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(color);
        g.setCornerRadius(px(radiusDp));
        return g;
    }

    private static ColorStateList states(int normal) {
        return new ColorStateList(new int[][] {
                {android.R.attr.state_focused}, {android.R.attr.state_pressed}, {}},
                new int[] {DARK, DARK, normal});
    }

    private LinearLayout card(LinearLayout parent, String title) {
        LinearLayout c = col();
        c.setPadding(px(18), px(14), px(18), px(18));
        c.setBackgroundDrawable(shape(CARD, 14));
        TextView t = text(title, 12, ACCENT, true);
        t.setPadding(0, 0, 0, px(10));
        c.addView(t);
        parent.addView(c, margin(-1, -2, 0, 0, 0, px(16)));
        cards.add(c);
        return c;
    }

    /** Botao com destaque forte de foco: essencial para navegar pelo controle remoto. */
    private TextView button(String label, boolean primary, View.OnClickListener l) {
        final TextView b = text(label, 16, TEXT, true);
        b.setGravity(Gravity.CENTER);
        b.setPadding(px(18), px(12), px(18), px(12));
        b.setFocusable(true);
        b.setClickable(true);
        StateListDrawable bg = new StateListDrawable();
        bg.addState(new int[] {android.R.attr.state_focused}, shape(ACCENT, 10));
        bg.addState(new int[] {android.R.attr.state_pressed}, shape(ACCENT, 10));
        GradientDrawable normal = shape(primary ? 0xFF3A5BD0 : BTN, 10);
        bg.addState(new int[] {}, normal);
        b.setBackgroundDrawable(bg);
        b.setTextColor(states(TEXT));
        b.setOnClickListener(l);
        b.setOnFocusChangeListener(new View.OnFocusChangeListener() {
            public void onFocusChange(View v, boolean f) {
                v.animate().scaleX(f ? 1.04f : 1f).scaleY(f ? 1.04f : 1f).setDuration(120).start();
            }
        });
        return b;
    }

    private void permRow(LinearLayout parent, TextView dot, String title, TextView sub, View btn) {
        LinearLayout r = row();
        r.setGravity(Gravity.CENTER_VERTICAL);
        r.addView(dot);
        LinearLayout t = col();
        t.addView(text(title, 16, TEXT, false));
        t.addView(sub);
        r.addView(t, new LinearLayout.LayoutParams(0, -2, 1));
        if (btn != null) r.addView(btn, margin(-2, -2, px(10), 0, 0, 0));
        parent.addView(r, margin(-1, -2, 0, px(6), 0, px(6)));
    }

    private void toast(String s) {
        Toast.makeText(this, s, Toast.LENGTH_LONG).show();
    }
}
