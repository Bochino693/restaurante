# Início Rápido

Substituto leve do app "AutoStart" (com.autostart 2.2) para abrir um jogo/app
assim que a TV box liga. Feito para aparelhos fracos (Mali-450, 1 GB de RAM).

- `inicio-rapido.apk` — APK pronto para instalar (Android 4.1+, ~33 KB).
- `./build.sh` — recompila sem Gradle.

## Como funciona

**Modo rápido (necessário no Android 10+):** o Início Rápido é definido como
tela inicial da box. O Android abre a tela inicial assim que termina de ligar,
e ela abre o jogo na hora. Depois disso o botão Home leva ao launcher de
fábrica (ou de volta ao jogo, com "Preso no jogo" ligado).

**Modo compatível (Android 9 ou anterior):** sem trocar a tela inicial, o jogo
abre no primeiro aviso de boot. No Android 10+ o sistema bloqueia esse caminho
(testado numa box Android 10 / API 29: nada abre).

A tela mostra, em "Última inicialização", quantos segundos depois de ligar o
jogo foi aberto e por qual caminho.

## Compilar

    apt-get install aapt apksigner zipalign dalvik-exchange default-jdk
    ANDROID_JAR=/caminho/android.jar ./build.sh

`ANDROID_JAR` precisa ser API 24 ou mais nova (atributo `directBootAware`).
Serve o `android.jar` do SDK ou o `org.robolectric:android-all` do Maven Central.

A keystore deste diretório é descartável e serve só para que novas versões
instalem por cima da anterior.
