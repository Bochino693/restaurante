# Início Rápido

Substituto leve do app "AutoStart" (com.autostart 2.2) para abrir um jogo/app
assim que a TV box liga. Feito para aparelhos fracos (Mali-450, 1 GB de RAM).

- `inicio-rapido.apk` — APK pronto para instalar (Android 4.1+, ~33 KB).
- `./build.sh` — recompila sem Gradle.

## Como funciona

Igual ao AutoStart: o launcher normal da box sobe e o jogo abre em seguida.
Não é um launcher e não pede para virar app padrão de nada.

| | AutoStart original | Início Rápido |
|---|---|---|
| Gatilho | só `BOOT_COMPLETED`, sem prioridade | `LOCKED_BOOT_COMPLETED`, `BOOT_COMPLETED` (prioridade 999), armazenamento montado, rede — o que chegar primeiro |
| Depois do gatilho | agenda alarme (≥ 0,3 s) e só então abre | abre na hora (atraso 0) |
| Código | 711 KB (support-v4 inteira) | 26 KB, sem bibliotecas |
| Apps só de TV (leanback) | não abre | abre |
| Permissões | nenhuma tela | botões dentro do app |

A tela mostra, em "Última inicialização", quantos segundos depois de ligar o
jogo foi aberto e por qual aviso — ou o motivo da falha.

## Compilar

    apt-get install aapt apksigner zipalign dalvik-exchange default-jdk
    ANDROID_JAR=/caminho/android.jar ./build.sh

`ANDROID_JAR` precisa ser API 24 ou mais nova (atributo `directBootAware`).
Serve o `android.jar` do SDK ou o `org.robolectric:android-all` do Maven Central.

A keystore deste diretório é descartável e serve só para que novas versões
instalem por cima da anterior.
