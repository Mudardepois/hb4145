"""Diagnóstico da comunicação GPIB (Keysight 82357B + HP 4145B).

Rode no computador onde o adaptador está conectado, usando o Python do projeto:

    .venv/bin/python diagnostico_gpib.py
"""

import glob
import os
import platform
import signal
import subprocess

import pyvisa

if os.path.exists("/.flatpak-info"):
    print("\nATENÇÃO: este Python está rodando DENTRO de um Flatpak.")
    print("  O /usr visto aqui é o do runtime do Flatpak, não o do host.")
    print("  O Keysight IOLS instalado no host não é visível neste sandbox.")
    print("  Rode este script em um terminal do host (fora do Flatpak).\n")

ENDERECO_GPIB = "GPIB0::17::INSTR"
DIR_KEYSIGHT = "/opt/keysight/iolibs"

CAMINHOS_VISA_KEYSIGHT = [
    "/usr/lib/x86_64-linux-gnu/libiovisa.so",
    "/usr/lib64/libiovisa.so",
    DIR_KEYSIGHT + "/libktvisa32.so",
    DIR_KEYSIGHT + "/libktvisa32.so.0.0.0",
]

DIRETORIOS_BUSCA = [
    DIR_KEYSIGHT,
    DIR_KEYSIGHT + "/ivivisa",
    "/usr/lib64",
    "/usr/lib64/ivivisa",
    "/usr/lib/x86_64-linux-gnu",
    "/usr/lib/x86_64-linux-gnu/ivivisa",
    "/usr/local/lib64",
    "/usr/local/lib",
    "/lib64",
    "/var/lib/ivivisa",
]


def ler_arquivo(caminho, limite=None):
    try:
        with open(caminho, encoding="utf-8", errors="replace") as arquivo:
            linhas = arquivo.readlines()
    except OSError as e:
        return f"Não foi possível ler {caminho}: {e}"
    if limite:
        linhas = linhas[-limite:]
    return "".join(linhas).strip()


def procurar_sistema(nome):
    print(f"\nBusca em todo o sistema por {nome}* (pode demorar)...")
    try:
        resultado = subprocess.run(
            ["find", "/", "-xdev", "-name", nome + "*"],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        print("  A busca demorou mais de 90 s. Rode manualmente:")
        print(f"    sudo find / -name '{nome}*' 2>/dev/null")
        return
    except Exception as e:
        print("  Não foi possível executar 'find':", e)
        return
    achados = [linha for linha in (resultado.stdout or "").splitlines() if linha.strip()]
    if achados:
        for linha in achados:
            print("   ", linha)
    else:
        print("    NÃO ENCONTRADA em nenhum lugar do sistema.")


def cache_linker():
    print("\nCache do linker (ldconfig -p):")
    try:
        resultado = subprocess.run(
            ["ldconfig", "-p"], capture_output=True, text=True, timeout=30
        )
    except Exception as e:
        print("  Não foi possível executar 'ldconfig -p':", e)
        return
    linhas = [
        linha.strip()
        for linha in (resultado.stdout or "").splitlines()
        if any(k in linha.lower() for k in ("ivivisa", "ktvisa", "iovisa", "gpib"))
    ]
    if linhas:
        for linha in linhas:
            print("  ", linha)
    else:
        print("  Nenhuma entrada de VISA/GPIB no cache.")


def listar_diretorio(diretorio):
    print(f"\nConteúdo de {diretorio} (libs de VISA/GPIB):")
    if not os.path.isdir(diretorio):
        print("  (não existe)")
        return
    try:
        nomes = sorted(os.listdir(diretorio))
    except OSError as e:
        print("  Não foi possível listar:", e)
        return
    interessantes = [
        nome
        for nome in nomes
        if any(k in nome.lower() for k in ("ivivisa", "ktvisa", "iovisa", "gpib"))
    ]
    if interessantes:
        for nome in interessantes:
            print("  ", nome)
    else:
        print("  (nenhuma lib de VISA/GPIB encontrada)")


def inspecionar_biblioteca(caminho):
    print(f"\n--- Dependências de {caminho} ---")
    try:
        resultado = subprocess.run(
            ["ldd", caminho], capture_output=True, text=True, timeout=30
        )
    except Exception as e:
        print("Não foi possível executar 'ldd':", e)
        return
    saida = (resultado.stdout or "") + (resultado.stderr or "")
    print(saida.strip())
    faltando = [linha.strip() for linha in saida.splitlines() if "not found" in linha]
    if faltando:
        print("\n>>> DEPENDÊNCIAS FALTANDO:")
        for linha in faltando:
            print("   ", linha)
        print("\nSugestão: localize essas bibliotecas (o script procura abaixo) e")
        print("adicione o diretório ao carregador dinâmico, por exemplo:")
        print('  echo "/opt/keysight/iolibs" | sudo tee /etc/ld.so.conf.d/keysight-iolibs.conf')
        print("  sudo ldconfig")
    else:
        print("\n>>> Todas as dependências foram encontradas.")


def procurar(nome):
    print(f"\nProcurando {nome}:")
    achou = False
    for diretorio in DIRETORIOS_BUSCA:
        if not os.path.isdir(diretorio):
            continue
        padrao = os.path.join(diretorio, "**", nome + "*")
        for caminho in glob.glob(padrao, recursive=True):
            print("   ", caminho)
            achou = True
    if not achou:
        print("    NÃO ENCONTRADA nos diretórios usuais.")


def executar(comando):
    try:
        resultado = subprocess.run(
            comando, capture_output=True, text=True, timeout=20
        )
    except Exception as e:
        print("  Falha ao executar", comando[0], ":", e)
        return
    saida = (resultado.stdout or "").strip()
    if saida:
        for linha in saida.splitlines():
            print("  ", linha)
    if resultado.returncode != 0:
        erro = (resultado.stderr or "").strip()
        if erro:
            for linha in erro.splitlines():
                print("  stderr:", linha)


def status_hardware():
    print("\n=== Status do hardware/drivers ===")

    print("\n-- Adaptador no lsusb --")
    try:
        resultado = subprocess.run(
            ["lsusb"], capture_output=True, text=True, timeout=20
        )
        linhas = [
            linha
            for linha in (resultado.stdout or "").splitlines()
            if any(k in linha.lower() for k in ("keysight", "agilent", "0957"))
        ]
        if linhas:
            for linha in linhas:
                print("  ", linha)
        else:
            print("  adaptador Keysight/Agilent não encontrado no lsusb")
    except Exception as e:
        print("  Falha ao executar lsusb:", e)

    print("\n-- Módulos de kernel (gpib/agilent/kt/keysight) --")
    try:
        resultado = subprocess.run(
            ["lsmod"], capture_output=True, text=True, timeout=20
        )
        linhas = [
            linha
            for linha in (resultado.stdout or "").splitlines()
            if any(k in linha.lower() for k in ("gpib", "agilent", "kt_", "keysight"))
        ]
        if linhas:
            for linha in linhas:
                print("  ", linha)
        else:
            print("  nenhum módulo relacionado carregado")
    except Exception as e:
        print("  Falha ao executar lsmod:", e)

    print("\n-- dkms status --")
    executar(["dkms", "status"])

    print("\n-- Dispositivos /dev/gpib* --")
    dispositivos = sorted(glob.glob("/dev/gpib*"))
    if dispositivos:
        for caminho in dispositivos:
            print("  ", caminho)
    else:
        print("  nenhum /dev/gpib*")

    print("\n-- /etc/gpib.conf --")
    if os.path.exists("/etc/gpib.conf"):
        print(ler_arquivo("/etc/gpib.conf"))
    else:
        print("  não existe")

    print("\n-- Pacote de firmware do linux-gpib --")
    try:
        resultado = subprocess.run(
            ["rpm", "-q", "linux-gpib-firmware"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if resultado.returncode == 0:
            print("  ", (resultado.stdout or "").strip())
        else:
            print("  linux-gpib-firmware NÃO instalado")
    except Exception as e:
        print("  Falha ao consultar o rpm:", e)

    print("\n-- Serviços da Keysight --")
    try:
        resultado = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--all", "--no-pager"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        linhas = [
            linha
            for linha in (resultado.stdout or "").splitlines()
            if any(k in linha.lower() for k in ("keysight", "io-ds", "kt-", "kccs", "kiis"))
        ]
        if linhas:
            for linha in linhas:
                print("  ", linha)
        else:
            print("  nenhum serviço da Keysight listado")
    except Exception as e:
        print("  Falha ao executar systemctl:", e)

    print("\n-- Últimas linhas de /var/log/keysight/iolibs/iols.log --")
    if os.path.exists("/var/log/keysight/iolibs/iols.log"):
        print(ler_arquivo("/var/log/keysight/iolibs/iols.log", limite=20))
    else:
        print("  não existe")

    print("\n-- Últimas linhas de /var/log/keysight/iolibs/ds-logs/ds-hub.log --")
    if os.path.exists("/var/log/keysight/iolibs/ds-logs/ds-hub.log"):
        print(ler_arquivo("/var/log/keysight/iolibs/ds-logs/ds-hub.log", limite=20))
    else:
        print("  não existe")


class _TimeoutListagem(Exception):
    pass


def _alarme_listagem(signum, frame):
    raise _TimeoutListagem("list_resources demorou demais")


def listar_com_timeout(rm, segundos=15):
    anterior = signal.signal(signal.SIGALRM, _alarme_listagem)
    signal.setitimer(signal.ITIMER_REAL, segundos)
    try:
        return rm.list_resources()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, anterior)


def testar(nome, rm):
    print(f"\n=== Backend: {nome} ===")
    try:
        recursos = listar_com_timeout(rm, 15)
        print("Recursos encontrados:", recursos)
    except _TimeoutListagem:
        print("TIMEOUT: list_resources() não respondeu em 15 s.")
        print("  O serviço da Keysight ou o driver do adaptador pode estar parado.")
        return
    except Exception as e:
        print("Falha ao listar recursos:", type(e).__name__, "-", e)
        return

    if ENDERECO_GPIB not in recursos:
        print(f"ATENÇÃO: {ENDERECO_GPIB} não apareceu na lista.")
        print("Confira o endereço GPIB do HP 4145B (deve ser 17) e o cabo.")
        return

    try:
        inst = rm.open_resource(ENDERECO_GPIB)
        inst.timeout = 5000
        print("open_resource: OK")
        inst.clear()
        print("clear (Selected Device Clear): OK")
        inst.close()
        print("Comunicação GPIB básica funcionando.")
    except Exception as e:
        print("Falha ao abrir/comunicar:", type(e).__name__, "-", e)


print("PyVISA:", pyvisa.__version__)

print("\nSistema:")
print("  ", ler_arquivo("/etc/os-release").replace("\n", " | "))
print("   Kernel:", platform.release())

print("\nBibliotecas VISA da Keysight:")
encontradas = []
for caminho in CAMINHOS_VISA_KEYSIGHT:
    existe = os.path.exists(caminho)
    print(("  [ok] " if existe else "  [--] ") + caminho)
    if existe:
        encontradas.append(caminho)

if not encontradas:
    print("  Nenhuma encontrada. Para o 82357B, instale o Keysight IO")
    print("  Libraries Suite para Linux (ou use linux-gpib com o backend @py).")

for caminho in encontradas:
    inspecionar_biblioteca(caminho)

procurar("libivivisa-utilities.so")
procurar("libivivisa.so")
procurar_sistema("libivivisa")
cache_linker()
listar_diretorio("/usr/lib64")
listar_diretorio("/usr/lib/x86_64-linux-gnu")
listar_diretorio(DIR_KEYSIGHT)

print("\nPacotes RPM relacionados (ivi/visa/keysight/iolib/gpib):")
try:
    resultado = subprocess.run(
        ["rpm", "-qa"], capture_output=True, text=True, timeout=30
    )
    pacotes = [
        linha
        for linha in (resultado.stdout or "").splitlines()
        if any(k in linha.lower() for k in ("ivi", "visa", "keysight", "iolib", "gpib"))
    ]
    if pacotes:
        for pacote in sorted(pacotes):
            print("  ", pacote)
    else:
        print("  Nenhum pacote RPM encontrado.")
except Exception as e:
    print("  Não foi possível executar 'rpm -qa':", e)

print("\nConteúdo de /opt/keysight/iolibsprerequisites:")
if os.path.isdir("/opt/keysight/iolibsprerequisites"):
    total = 0
    for raiz, dirs, arquivos in os.walk("/opt/keysight/iolibsprerequisites"):
        profundidade = raiz[len("/opt/keysight/iolibsprerequisites"):].count(os.sep)
        if profundidade > 2:
            dirs[:] = []
            continue
        for arquivo in sorted(arquivos):
            print("  ", os.path.join(raiz, arquivo))
            total += 1
            if total >= 60:
                print("   ... (lista truncada)")
                break
        if total >= 60:
            break
    if total == 0:
        print("  (diretório vazio)")
else:
    print("  (não existe)")

print("\nConteúdo de /var/log/keysight:")
if os.path.isdir("/var/log/keysight"):
    for raiz, dirs, arquivos in os.walk("/var/log/keysight"):
        for arquivo in sorted(arquivos):
            print("  ", os.path.join(raiz, arquivo))
else:
    print("  (não existe)")

for link in ("/usr/lib64/libiovisa.so", "/usr/lib/x86_64-linux-gnu/libiovisa.so"):
    if os.path.islink(link):
        alvo = os.readlink(link)
        existe = os.path.exists(link)
        print(f"\n{link} -> {alvo} ({'OK' if existe else 'ALVO NÃO EXISTE'})")

print("\nConfiguração do carregador dinâmico (/etc/ld.so.conf.d):")
achou_conf = False
for caminho in glob.glob("/etc/ld.so.conf.d/*"):
    nome = os.path.basename(caminho).lower()
    if "keysight" in nome or "iolib" in nome or "ivi" in nome:
        achou_conf = True
        print(f"\n--- {caminho} ---")
        try:
            with open(caminho, encoding="utf-8", errors="replace") as arquivo:
                print(arquivo.read().strip())
        except OSError as e:
            print("Não foi possível ler:", e)
if not achou_conf:
    print("  Nenhum arquivo .conf da Keysight/IVI encontrado.")

log = "/var/log/keysight/iolibs/kt_iolibs_install_log"
print(f"\nLog de instalação ({log}):")
if os.path.exists(log):
    print(ler_arquivo(log, limite=80))
else:
    print("  Log não encontrado (a instalação pode ter sido incompleta).")

log_kernel = "/var/log/keysight/iolibs/kt_kernel_install_log"
print(f"\nLog do driver de kernel ({log_kernel}):")
if os.path.exists(log_kernel):
    print(ler_arquivo(log_kernel, limite=40))
else:
    print("  Log não encontrado.")

status_hardware()

for caminho in encontradas:
    try:
        rm = pyvisa.ResourceManager(caminho)
    except Exception as e:
        print(f"\nNão foi possível carregar {caminho}: {type(e).__name__} - {e}")
        continue
    testar(f"Keysight VISA ({caminho})", rm)
    # Carregar uma segunda VISA no mesmo processo causa segmentation fault.
    break

try:
    testar("pyvisa-py (@py / linux-gpib)", pyvisa.ResourceManager("@py"))
except Exception as e:
    print("\nBackend @py indisponível:", type(e).__name__, "-", e)
