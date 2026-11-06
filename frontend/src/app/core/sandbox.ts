import { WorkerStatus } from './models';

/**
 * Classificação de HOST SANDBOX (Colab / Kaggle) — base do par de botões
 * Ligar/Desligar do Perfil.
 *
 * Os dois são a mesma coisa do ponto de vista do app: VM efêmera que morre
 * sozinha e cujo worker se registra no primeiro heartbeat. O que muda é só
 * pra onde a URL aponta e qual plataforma nomear no texto.
 *
 * Por que um módulo à parte (e não um método no `PerfilComponent`): o
 * componente tem ~100 KB e nenhum spec; essas três funções são puras e é
 * justamente onde mora o bug que motivou a extração — o botão só existia
 * pra quem tinha "colab" no nome, então o Kaggle (cujo `display_name` é o
 * hash do container, ex.: "fc1027582ecb") ficava sem botão nenhum.
 */

/** host_ids de Kaggle já observados em campo. O `display_name` de um runtime
 * Kaggle é o hash do container e pode não conter "kaggle" — então o nome
 * sozinho não identifica. Mesma armadilha que o Colab resolve casando
 * "02151543". Manter em sincronia com `KAGGLE_HOST_IDS` do backend. */
export const KAGGLE_HOST_IDS: readonly string[] = [
  '558846ca-3973-40d1-ab7c-9dff20b97d5c', // runtime GPU (T4 x2)
  'd060d384-ca72-427c-b559-fddaafb0c9ca', // runtime CPU
];

/** host_ids de Colab conhecidos (prefixo do container). O display_name de um
 * Colab também pode ser o hash ("9379fcb97906"), então o nome sozinho não
 * basta. Mantido curto de propósito: só os que já apareceram em campo. */
export const COLAB_HOST_IDS: readonly string[] = [
  '02151543', // Colab 2 (conta 1)
];

/** Notebook Kaggle padrão quando o host não declara `kaggle_notebook_url`. */
export const DEFAULT_KAGGLE_NOTEBOOK_URL = 'https://www.kaggle.com/code/diegoaraujo85/notebooke5f51f72d8';

/** Notebook Colab padrão (fallback de `colab_notebook_id`). */
export const DEFAULT_COLAB_NOTEBOOK_ID = '1WRlvNXyQGyYZ9xURL5gN-7ZmmdHRBi7A';

export type SandboxKind = 'colab' | 'kaggle';

function identity(w: WorkerStatus): string {
  return `${w.display_name ?? ''} ${w.hostname ?? ''} ${w.host_id ?? ''}`.toLowerCase();
}

/** 'colab' | 'kaggle' | null. Kaggle tem precedência: um notebook Kaggle não
 * pode ser confundido com Colab (a URL é totalmente diferente). */
export function sandboxKind(w: WorkerStatus): SandboxKind | null {
  const id = (w.host_id ?? '').toLowerCase();
  const texto = identity(w);
  if (
    KAGGLE_HOST_IDS.some((k) => k.toLowerCase() === id) ||
    texto.includes('kaggle') ||
    !!w.kaggle_notebook_url
  ) {
    return 'kaggle';
  }
  if (texto.includes('colab')) return 'colab';
  // host_id conhecido de Colab (prefixo do container, ex.: "02151543"): o
  // display_name pode ser só o hash, sem a palavra "colab" em lugar nenhum.
  if (COLAB_HOST_IDS.some((c) => id.startsWith(c))) return 'colab';
  return null;
}

/** Nome da plataforma pro texto do botão/link ("↗ Kaggle"). */
export function sandboxLabel(w: WorkerStatus): string | null {
  const kind = sandboxKind(w);
  if (kind === 'kaggle') return 'Kaggle';
  if (kind === 'colab') return 'Colab';
  return null;
}

/**
 * URL do notebook que o botão Ligar abre (e que o link "↗ <plataforma>"
 * pré-popula). `null` = host não é sandbox.
 *
 * Kaggle: página do notebook + `/edit` (idempotente — não duplica `/edit`).
 * Sem `authuser`: é parâmetro do Colab, e no Kaggle a conta é a do navegador.
 *
 * Colab: `/drive/<notebook>?authuser=<0|1>`, com heurística de conta quando
 * o `worker_status` não trouxe `colab_authuser` ("colab 2"/"02151543" → 1).
 */
export function sandboxNotebookUrl(w: WorkerStatus): string | null {
  const kind = sandboxKind(w);
  if (kind === 'kaggle') {
    const base = (w.kaggle_notebook_url || DEFAULT_KAGGLE_NOTEBOOK_URL).replace(/\/+$/, '');
    return base.endsWith('/edit') ? base : `${base}/edit`;
  }
  if (kind === 'colab') {
    const notebookId = w.colab_notebook_id || DEFAULT_COLAB_NOTEBOOK_ID;
    let authuser = w.colab_authuser ?? null;
    if (!authuser) {
      const s = identity(w);
      authuser = s.includes('colab 2') || s.includes('colab2') || s.includes('02151543') ? '1' : '0';
    }
    return `https://colab.research.google.com/drive/${notebookId}?authuser=${authuser}`;
  }
  return null;
}
