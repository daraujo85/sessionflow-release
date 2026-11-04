# Automação do "Ligar" de um Colab — roda DENTRO do browser-harness
# (`browser-harness < tools/colab_autostart_bh.py`), helpers pré-importados.
# Chamado por `tools/colab-autostart.sh`; não rodar direto.
#
# Env: SF_COLAB_URL (notebook + authuser, vindo de POST /workers/{id}/start).
#      SF_COLAB_RESUME=1 → não abre aba nem dispara Cmd+F9: reusa a aba do
#      notebook, espera o HUMANO resolver o reCAPTCHA (até SF_COLAB_ONLINE_TIMEOUT)
#      e segue confirmando os diálogos normais. Usado pelo .sh após o FAIL.
# Fluxo validado manualmente em 2026-10-23: abrir notebook → Cmd+F9 →
# "Executar assim mesmo" (aviso de notebook de terceiros) → "Permitir acesso"
# (1x por secret por notebook). A célula selfboot faz o resto; quem confirma
# o 🟢 é o heartbeat do worker (o script de shell espera por ele).
#
# reCAPTCHA "Você ainda está aí?" — NUNCA é clicado/resolvido aqui (proibido,
# risco de ban da conta). Ele é um teste de presença do Google; o que este
# script faz é só se comportar como um usuário comum antes de pedir execução:
#   REDUZ (ou tende a reduzir — não há métrica do Google, efeito não medido):
#     - disparar o Cmd+F9 milissegundos após o load, com o runtime ainda
#       "Reconectar"/desconectado (padrão de bot): agora, se a página mostra
#       Conectar/Reconectar, esperamos o notebook estabilizar (contagem de
#       células e rede quietas, até 30s) antes de executar;
#     - executar sem nenhuma interação prévia: focamos a 1ª célula com um clique
#       real (Input.dispatchMouseEvent → isTrusted) e saímos do modo edição com
#       Escape, como alguém que abriu o notebook e vai rodar.
#   NÃO REDUZ:
#     - Chrome --headless=new (UA "HeadlessChrome"): sinal forte de automação.
#       Não mascaramos o UA (seria burlar detecção). Se o captcha ficar
#       frequente, use o modo visível (sem --headless);
#     - religar o runtime muitas vezes seguidas / logo após hibernar — a
#       cadência é de quem chama o autostart; espaçar os religamentos ajuda;
#     - reputação da conta/IP e limites do Colab grátis ("Sessões em excesso").
import os
import time

URL = os.environ["SF_COLAB_URL"]
RESUME = os.environ.get("SF_COLAB_RESUME") == "1"
HUMAN_TIMEOUT = int(os.environ.get("SF_COLAB_ONLINE_TIMEOUT", "900"))
SETTLE_TIMEOUT = 30
CONFIRM = ("Executar assim mesmo", "Permitir acesso", "Continuar mesmo assim")
CAPTCHA = "Você ainda está aí?"
FATAL = {
    "Sessões em excesso": "conta no limite de sessões Colab — desligue outra",
    "Fazer login": "perfil sem login Google — rode tools/colab-autostart.sh --login",
    CAPTCHA: "reCAPTCHA de presença do Colab — precisa de humano (não automatizável)",
}
# Botão de conexão da toolbar quando o runtime NÃO está ligado.
DISCONNECTED = ("Conectar", "Reconectar", "Connect", "Reconnect")

def _nodes():
    return cdp("Accessibility.getFullAXTree")["nodes"]

def _name(n):
    return (n.get("name") or {}).get("value", "")

def _role(n):
    return (n.get("role") or {}).get("value")

def _click(bid):
    q = cdp("DOM.getBoxModel", backendNodeId=bid)["model"]["content"]
    click_at_xy(sum(q[0::2]) / 4, sum(q[1::2]) / 4)

def _button(nodes, name):
    for n in nodes:
        if _role(n) == "button" and _name(n) == name and n.get("backendDOMNodeId"):
            return n["backendDOMNodeId"]

def _texts(nodes):
    return " ".join(_name(n) for n in nodes if _role(n) in ("heading", "StaticText", "button"))

def _fatal(nodes):
    texts = _texts(nodes)
    for marker, reason in FATAL.items():
        if marker in texts:
            return reason

def _cells():
    return js("document.querySelectorAll('.cell').length") or 0

def _disconnected(nodes):
    return any(_role(n) == "button" and _name(n).strip() in DISCONNECTED for n in nodes)

def _settle():
    """Runtime desligado → espera o notebook parar de mudar (até 30s)."""
    end, last, stable = time.time() + SETTLE_TIMEOUT, -1, 0
    while time.time() < end and stable < 3:
        n = _cells()
        stable = stable + 1 if n == last else 0
        last = n
        time.sleep(1)
    try:
        wait_for_network_idle(timeout=max(1.0, end - time.time()), idle_ms=1500)
    except Exception:
        pass  # Colab mantém long-poll aberto; "quase quieto" basta

def _focus_first_cell():
    """Clique real no corpo da 1ª célula + Escape (sai do modo edição)."""
    box = js(
        "(() => { const c = document.querySelector('.cell'); if (!c) return null;"
        " c.scrollIntoView({block: 'center'}); const r = c.getBoundingClientRect();"
        " return [r.left + Math.min(r.width / 2, 200), r.top + Math.min(r.height / 2, 20)]; })()"
    )
    if box:
        click_at_xy(box[0], box[1])
        time.sleep(0.5)
    press_key("Escape")

def _confirm_dialogs():
    # Diálogos aparecem em sequência (aviso + 1 por secret); varre até sossegar.
    clicks, quiet = 0, 0
    while quiet < 8:
        nodes = _nodes()
        reason = _fatal(nodes)
        if reason:
            raise SystemExit(f"FAIL {reason}")
        hit = next((b for b in (_button(nodes, c) for c in CONFIRM) if b), None)
        if hit:
            _click(hit)
            clicks += 1
            quiet = 0
        else:
            quiet += 1
        time.sleep(2)
    return clicks

def _notebook_tab():
    nb_id = URL.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    for t in list_tabs(include_chrome=False):
        if nb_id in (t.get("url") or ""):
            return t

if RESUME:
    tab = _notebook_tab()
    if not tab:
        raise SystemExit("FAIL aba do notebook não está mais aberta")
    switch_tab(tab, activate=True)
    # Só LÊ a página: quem resolve o captcha é o humano, na própria aba.
    end = time.time() + HUMAN_TIMEOUT
    while time.time() < end and CAPTCHA in _texts(_nodes()):
        time.sleep(5)
    if CAPTCHA in _texts(_nodes()):
        raise SystemExit("FAIL captcha não resolvido a tempo")
    print(f"OK captcha resolvido ({_confirm_dialogs()} confirmações)")
    raise SystemExit(0)

new_tab(URL)
wait_for_load()
deadline = time.time() + 60
while time.time() < deadline and not _cells():
    time.sleep(1)
if not _cells():
    reason = _fatal(_nodes()) or "notebook não carregou (sem células)"
    raise SystemExit(f"FAIL {reason}")

nodes = _nodes()
reason = _fatal(nodes)
if reason:
    raise SystemExit(f"FAIL {reason}")
if _disconnected(nodes):
    _settle()
_focus_first_cell()
cdp("Input.dispatchKeyEvent", type="keyDown", key="F9", code="F9", modifiers=4, windowsVirtualKeyCode=120)
cdp("Input.dispatchKeyEvent", type="keyUp", key="F9", code="F9", modifiers=4, windowsVirtualKeyCode=120)

print(f"OK executando ({_confirm_dialogs()} confirmações)")
