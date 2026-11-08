import pyvisa
import time
import matplotlib.pyplot as plt
import os
import re
from datetime import datetime
import csv

# adaptador: Keysight 82357B 

nome_da_medida = "Teste_Leser"

corrente_inicial = 0.0  #A
corrente_final = 0.06  #A
compliance_v = 3.0  # V
numero_pontos = 100  #Quantidade de pontos medidos

escala_max_mon1 = 5.0  #V Valor máximo para o eixo Y2

# Path do driver VISA (Fedora/RHEL usa /usr/lib64; Debian/Ubuntu usa /usr/lib/x86_64-linux-gnu)
keysight_path = '/usr/lib64/libiovisa.so'

passo = (corrente_final - corrente_inicial) / (numero_pontos - 1)

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

    print("Configurando o HP 4145B...")
    inst.write("DE CH 1; CH 2; CH 3, 'V3', 'I3', 2, 1; CH 4;")
    inst.write("DE VM 1, 'MON1'")
    inst.write("DE VM 2; DE VS 1; DE VS 2")

    cmd_sweep = f"SS IR 1, {corrente_inicial:.6f}, {corrente_final:.6f}, {passo:.6f}, {compliance_v:.6f}"
    inst.write(cmd_sweep)

    inst.write(f"SM DM1 XN 'I3', 1, {corrente_inicial:.6f}, {corrente_final:.6f}")
    inst.write(f"SM DM1 YA 'V3', 1, 0, {compliance_v:.6f}")
    inst.write(f"SM DM1 YB 'MON1', 1, 0, {escala_max_mon1:.6f}")

    tempo_espera = .1 + (numero_pontos * 0.02)
    print(f"Medindo, Tempo estimado: {tempo_espera:.0f} s")

    inst.write("BC")
    inst.write("MD ME1")
    time.sleep(tempo_espera)

    # =========================================================
    # LEITURA DOS DADOS MEDIDOS
    # =========================================================
    # No 4145B o bit "Data Ready" do status byte é resetado quando a
    # transferência começa. Por isso, cada canal precisa de uma medição
    # própria: BC limpa o buffer, MD ME1 mede e DO 'canal' transfere.
    def ler_canal(canal, rotulo):
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
            # Se for uma string única com vírgulas, a primeira leitura já
            # traz todos os pontos e o loop sai.
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

    raw_v3, tensao_v3 = ler_canal('V3', 'V3')

    inst.write("BC")
    inst.write("MD ME1")
    time.sleep(tempo_espera)
    raw_mon1, tensao_mon1 = ler_canal('MON1', 'MON1')

    if len(tensao_v3) != numero_pontos:
        print(f"Aviso: V3 retornou {len(tensao_v3)} pontos, esperado {numero_pontos}.")
    if len(tensao_mon1) != numero_pontos:
        print(f"Aviso: MON1 retornou {len(tensao_mon1)} pontos, esperado {numero_pontos}.")

    #for i in (0,len(tensao_mon1)-1):
    #    tensao_mon1[i] = tensao_mon1[i]*(-1)


    print(f"Sucesso! {len(tensao_v3)} pontos de V3 e {len(tensao_mon1)} pontos de MON1.")

    corrente_teorica = [corrente_inicial + (i * passo) for i in range(numero_pontos)]

    tamanho_final = min(len(tensao_v3), len(tensao_mon1), len(corrente_teorica))
    corrente = corrente_teorica[:tamanho_final]
    tensao_v3 = tensao_v3[:tamanho_final]
    tensao_mon1 = tensao_mon1[:tamanho_final]

    if tamanho_final > 0:
        # CSV

        agora = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_pasta = f"{agora}_{nome_da_medida}"
        os.makedirs(nome_pasta, exist_ok=True)

        caminho_csv = os.path.join(nome_pasta, f"{nome_da_medida}.csv")
        caminho_png = os.path.join(nome_pasta, f"{nome_da_medida}.png")

        with open(caminho_csv, mode='w', newline='') as arquivo_csv:
            escritor = csv.writer(arquivo_csv)
            escritor.writerow(['Corrente_I3_A', 'Tensao_V3_V', 'Tensao_MON1_V'])
            for i in range(tamanho_final):
                escritor.writerow([corrente[i], tensao_v3[i], tensao_mon1[i]])
        print(f"Dados salvos em: {caminho_csv}")

        # Plot
        fig, ax1 = plt.subplots(figsize=(9, 6))

        # Eixo Y para V3 (SMU3)
        cor_v3 = 'tab:red'
        ax1.set_xlabel('Corrente Forçada [A]', fontweight='bold')
        ax1.set_ylabel('Tensão V3 [V] (SMU3)', color=cor_v3, fontweight='bold')
        linha1 = ax1.plot(corrente, tensao_v3, marker='o', markersize=4, linestyle='-', color=cor_v3, label='V3 (SMU3)')
        ax1.tick_params(axis='y', labelcolor=cor_v3)
        ax1.grid(True)

        # Eixo Y para MON1 (VM1)
        ax2 = ax1.twinx()
        cor_mon1 = 'tab:blue'
        ax2.set_ylabel('Tensão VM1 [V]', color=cor_mon1, fontweight='bold')
        linha2 = ax2.plot(corrente, tensao_mon1, marker='s', markersize=4, linestyle='--', color=cor_mon1,
                          label='VM1')
        ax2.tick_params(axis='y', labelcolor=cor_mon1)

        linhas = linha1 + linha2
        rotulos = [l.get_label() for l in linhas]
        ax1.legend(linhas, rotulos, loc='upper left')

        plt.title(f'Curva I-V - {nome_da_medida}\n(Compliance: {compliance_v}V | Pontos: {numero_pontos})')

        plt.savefig(caminho_png, dpi=300, bbox_inches='tight')
        print(f"Gráfico salvo em: {caminho_png}")

        plt.show()
    else:
        print("Erro: Nenhum dado foi processado corretamente.")

except pyvisa.VisaIOError as e:
    print(f"Erro no VISA: {e}")
except Exception as e:
    print(f"Ocorreu um erro: {e}")