import pyvisa
import time
import matplotlib.pyplot as plt
import os
import re
from datetime import datetime
import csv

# =========================================================
# PARÂMETROS DA MEDIDA
# =========================================================
nome_da_medida = "Curva_IV"

canal_smu = 1          # Canal usado na varredura: 1, 2, 3 ou 4
tensao_inicial = 0.0   # V
tensao_final = 3.4     # V
numero_pontos = 300    # Quantidade de pontos medidos
compliance_a = 0.01    # A (limite de corrente da fonte de tensão)
timeout_medicao_s = 300.0  # Tempo máximo de espera pelo fim da medida (s)

# Definição do canal (ver manual do 4145B, página 3-104):
# SOURCE MODE:     1 = V (fonte de tensão), 2 = I (fonte de corrente), 3 = COM
# SOURCE FUNCTION: 1 = VAR1 (varredura principal), 2 = VAR2, 3 = CONST, 4 = VAR1'
# Para uma varredura de tensão no canal escolhido, usamos V + VAR1.
modo_fonte = 1
funcao_fonte = 1

# Path do driver VISA (Fedora/RHEL usa /usr/lib64; Debian/Ubuntu usa /usr/lib/x86_64-linux-gnu)
keysight_path = '/usr/lib64/libiovisa.so'


def parse_hp_data(raw_string):
    """Extrai todos os valores numéricos do bloco de dados do 4145B.

    O 4145B envia os pontos concatenados, cada um precedido por um
    caractere de status (N, L, V, X, C, T), por exemplo:

        N 1.6740E+00C 3.0000E+00C 3.0000E+00...

    Não há vírgula entre os pontos. A regex abaixo encontra todos os
    números (com sinal e expoente) e ignora os caracteres de status.
    """
    padrao = r'[+-]?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?'
    valores = []
    for texto in re.findall(padrao, raw_string):
        try:
            valores.append(float(texto))
        except ValueError:
            continue
    return valores


def abrir_resource_manager():
    """Abre a VISA que realmente tem suporte ao 82357B.

    O backend '@py' (pyvisa-py) só acessa GPIB no Linux através do linux-gpib
    (libgpib + gpib-ctypes). O adaptador Keysight 82357B, quando instalado com
    o Keysight IO Libraries Suite, é acessado por libiovisa.so/libktvisa32.so.
    """
    candidatos = [
        keysight_path,
        '/usr/lib64/libiovisa.so',
        '/opt/keysight/iolibs/libktvisa32.so',
        '/opt/keysight/iolibs/libktvisa32.so.0.0.0',
    ]
    erros = []

    for caminho in candidatos:
        if not os.path.exists(caminho):
            continue
        try:
            rm = pyvisa.ResourceManager(caminho)
            rm.list_resources()  # força a inicialização da biblioteca
            print(f"VISA da Keysight carregada: {caminho}")
            return rm
        except Exception as e:
            erros.append(f"{caminho}: {e}")
            if "libivivisa" in str(e) or "cannot open shared object file" in str(e):
                erros.append(
                    "  -> A instalação do Keysight IO Libraries está incompleta "
                    "(faltam as VISA Shared Components). Reinstale o IOLS."
                )

    # Fallback para quem usa linux-gpib em vez do IO Libraries Suite
    try:
        try:
            import gpib_ctypes  # noqa: F401
        except ImportError:
            import gpib  # noqa: F401
    except ImportError as e:
        erros.append(f"@py: linux-gpib/gpib-ctypes não instalado ({e})")
    else:
        rm = pyvisa.ResourceManager('@py')
        print("Usando backend pyvisa-py (@py) com linux-gpib.")
        return rm

    raise RuntimeError(
        "Nenhum backend VISA com suporte a GPIB disponível.\n"
        "Instale o Keysight IO Libraries Suite para Linux (recomendado para "
        "o 82357B) ou o linux-gpib.\n" + "\n".join(erros)
    )


def validar_parametros():
    if canal_smu not in (1, 2, 3, 4):
        raise ValueError("canal_smu deve ser 1, 2, 3 ou 4.")
    if numero_pontos < 2:
        raise ValueError("numero_pontos deve ser maior ou igual a 2.")
    if numero_pontos > 512:
        raise ValueError(
            "numero_pontos deve ser no máximo 512 "
            "(limite do 4145B em varredura simples)."
        )
    if compliance_a <= 0:
        raise ValueError("compliance_a deve ser maior que zero.")


def aguardar_medicao(inst, timeout=120.0):
    """Aguarda o bit Data Ready (bit 0) do status byte do 4145B."""
    inicio = time.time()
    while time.time() - inicio < timeout:
        try:
            stb = inst.read_stb()
            if stb & 0x01:
                return stb
        except pyvisa.VisaIOError:
            pass
        time.sleep(0.2)

    raise TimeoutError(
        f"O 4145B não sinalizou Data Ready em {timeout:.0f} s."
    )


def ler_canal(inst, canal, rotulo, numero_pontos):
    """Faz DO 'canal' e devolve a string crua e os valores numéricos."""
    print(f"Coletando dados ({rotulo})...")
    try:
        print(f"  Status byte antes de {rotulo}: 0x{inst.read_stb():02X}")
    except Exception as e:
        print(f"  Status byte indisponível: {e}")

    inst.write(f"DO '{canal}'")

    partes = []
    valores = []
    timeout_original = inst.timeout
    inst.timeout = 10000  # 10 s por leitura, para não esperar 60 s
    try:
        # Se o terminador for CR/LF por campo, lê várias linhas.
        # Se for uma string única, a primeira leitura já traz todos os pontos.
        while len(valores) < numero_pontos:
            linha = inst.read()
            partes.append(linha)
            valores = parse_hp_data("".join(partes))
    except pyvisa.VisaIOError as e:
        print(f"  {rotulo}: leitura parou com {len(valores)} pontos ({e})")
    finally:
        inst.timeout = timeout_original

    raw = "".join(partes)
    print(f"  {rotulo}: {len(raw)} bytes, {len(valores)} pontos")
    print(f"  {rotulo} raw: {raw[:200]!r}")
    return raw, valores


def main():
    validar_parametros()

    nome_v = f"V{canal_smu}"
    nome_i = f"I{canal_smu}"

    passo_teorico = (tensao_final - tensao_inicial) / (numero_pontos - 1)
    passo_comando = passo_teorico

    try:
        rm = abrir_resource_manager()
        print("Recursos VISA encontrados:", rm.list_resources())

        inst = rm.open_resource("GPIB0::17::INSTR")
        inst.timeout = 60000

        inst.clear()
        # O 4145B termina cada comando com ';', CR ou LF.
        inst.write_termination = '\n'
        # O 4145B NÃO envia EOI por padrão (bit 7 do switch HP-IB = OFF), então
        # read() com termination None ficaria esperando até o timeout.
        # Com o bit 6 = 0 (vírgula), o bloco de dados termina em CR LF.
        inst.read_termination = '\r\n'

        print("Configurando o HP 4145B para curva I-V...")
        print(f"  Canal: SMU{canal_smu} ({nome_v} / {nome_i})")
        print(f"  Varredura: {tensao_inicial} V -> {tensao_final} V")
        print(f"  Pontos: {numero_pontos} | Passo: {passo_teorico:.6g} V")
        print(f"  Compliance: {compliance_a} A")

        # Desliga todos os canais e define apenas o canal escolhido como
        # fonte de tensão (V/VAR1). Os demais ficam como NOT USE.
        canais = []
        for ch in range(1, 5):
            if ch == canal_smu:
                canais.append(
                    f"CH {ch}, '{'V' + str(ch)}', '{'I' + str(ch)}', "
                    f"{modo_fonte}, {funcao_fonte}"
                )
            else:
                canais.append(f"CH {ch}")
        inst.write("DE " + "; ".join(canais))

        # Desliga monitores de tensão (VM) e fontes de tensão auxiliares (VS).
        inst.write("DE VM 1; DE VM 2; DE VS 1; DE VS 2")

        # Configura a varredura linear de tensão (VAR1 em modo VR).
        cmd_sweep = (
            f"SS VR 1, {tensao_inicial:.6f}, {tensao_final:.6f}, "
            f"{passo_comando:.6f}, {compliance_a:.6f}"
        )
        inst.write(cmd_sweep)

        # Configura os eixos do gráfico interno do 4145B (não é o gráfico
        # final; o gráfico final é gerado pelo matplotlib).
        inst.write(
            f"SM DM1 XN '{nome_v}', 1, {tensao_inicial:.6f}, {tensao_final:.6f}"
        )
        inst.write(
            f"SM DM1 YA '{nome_i}', 1, {-abs(compliance_a):.6f}, "
            f"{abs(compliance_a):.6f}"
        )

        tempo_espera = .1 + (numero_pontos * 0.02)
        print(f"Medindo, Tempo estimado: {tempo_espera:.0f} s")

        # Limpa o buffer e o bit Data Ready antes da medida.
        inst.write("BC")
        inst.write("MD ME1")

        try:
            stb = aguardar_medicao(inst, timeout_medicao_s)
            print(f"  Data Ready após a medida (status byte: 0x{stb:02X}).")
        except TimeoutError as e:
            print(f"  Aviso: {e} Usando espera fixa de {tempo_espera:.0f} s.")
            time.sleep(tempo_espera)

        # =========================================================
        # LEITURA DOS DADOS MEDIDOS
        # =========================================================
        # Como só há um canal a ler, não é necessário repetir a medida.
        raw_i, corrente = ler_canal(inst, nome_i, nome_i, numero_pontos)

        if len(corrente) != numero_pontos:
            print(
                f"Aviso: {nome_i} retornou {len(corrente)} pontos, "
                f"esperado {numero_pontos}."
            )

        # Tensão teórica aplicada em cada ponto.
        tensao = [
            tensao_inicial + (i * passo_teorico)
            for i in range(numero_pontos)
        ]

        tamanho_final = min(len(tensao), len(corrente))
        tensao = tensao[:tamanho_final]
        corrente = corrente[:tamanho_final]

        print(
            f"Sucesso! {tamanho_final} pontos de {nome_v} e {nome_i}."
        )

        if tamanho_final > 0:
            # =========================================================
            # CSV
            # =========================================================
            agora = datetime.now().strftime("%Y%m%d_%H%M%S")
            nome_pasta = f"{agora}_{nome_da_medida}"
            os.makedirs(nome_pasta, exist_ok=True)

            caminho_csv = os.path.join(nome_pasta, f"{nome_da_medida}.csv")
            caminho_png = os.path.join(nome_pasta, f"{nome_da_medida}.png")

            with open(caminho_csv, mode='w', newline='') as arquivo_csv:
                escritor = csv.writer(arquivo_csv)
                escritor.writerow([f'Tensao_{nome_v}_V', f'Corrente_{nome_i}_A'])
                for i in range(tamanho_final):
                    escritor.writerow([tensao[i], corrente[i]])
            print(f"Dados salvos em: {caminho_csv}")

            # =========================================================
            # GRÁFICO
            # =========================================================
            fig, ax = plt.subplots(figsize=(9, 6))

            ax.plot(
                tensao, corrente,
                marker='o', markersize=4, linestyle='-', color='tab:blue'
            )
            ax.set_xlabel('Tensão Forçada [V]', fontweight='bold')
            ax.set_ylabel('Corrente Medida [A]', fontweight='bold')
            ax.grid(True)
            ax.ticklabel_format(axis='y', style='sci', scilimits=(0, 0))

            plt.title(
                f'Curva I-V - {nome_da_medida} (SMU{canal_smu})\n'
                f'Compliance: {compliance_a} A | Pontos: {numero_pontos}'
            )

            plt.savefig(caminho_png, dpi=300, bbox_inches='tight')
            print(f"Gráfico salvo em: {caminho_png}")

            plt.show()
        else:
            print("Erro: Nenhum dado foi processado corretamente.")

    except pyvisa.VisaIOError as e:
        print(f"Erro no VISA: {e}")
    except Exception as e:
        print(f"Ocorreu um erro: {e}")


if __name__ == "__main__":
    main()
