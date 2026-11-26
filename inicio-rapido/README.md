# Início Rápido

Substituto leve do app "AutoStart" (com.autostart 2.2) para abrir um jogo/app
assim que a TV box liga. Feito para aparelhos fracos (Mali-450, 1 GB de RAM).

- `inicio-rapido.apk` — APK pronto para instalar (Android 4.1+, ~33 KB).
- `./build.sh` — recompila sem Gradle (dependências no topo do script).

## Por que é mais rápido

| | AutoStart original | Início Rápido |
|---|---|---|
| Gatilho | `BOOT_COMPLETED` + alarme | Launcher (HOME): roda antes do `BOOT_COMPLETED` |
| Tamanho do código | 711 KB (support-v4 inteira) | 23 KB, sem bibliotecas |
| Notificação / "ir para Home" entre apps | sim | não |
| Apps só de TV (leanback) | não abre | abre |
| Controle remoto | campos de texto | só botões |

## Uso

1. Instale e abra **Início Rápido**.
2. **Escolher o jogo / app**.
3. **Ativar modo rápido** → escolha "Início Rápido" como tela inicial → **Sempre**.
4. Reinicie a TV box.

Sem o passo 3 o app funciona no modo compatível (espera o `BOOT_COMPLETED`,
como o original).

A keystore deste diretório é descartável e serve só para que novas versões
instalem por cima da anterior.
