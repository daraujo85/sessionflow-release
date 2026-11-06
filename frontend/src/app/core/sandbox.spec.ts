import { describe, expect, it } from 'vitest';
import { WorkerStatus } from './models';
import {
  KAGGLE_HOST_IDS,
  sandboxKind,
  sandboxLabel,
  sandboxNotebookUrl,
} from './sandbox';

function host(extra: Partial<WorkerStatus> = {}): WorkerStatus {
  return {
    host_id: 'h1',
    hostname: 'h1',
    online: false,
    uptime_seconds: null,
    started_at: null,
    updated_at: null,
    ...extra,
  };
}

// O botão Ligar/Desligar existia só pra host com "colab" no nome. O Kaggle é
// sandbox igual (VM efêmera, worker que se registra no 1º heartbeat) mas o
// display_name dele é o hash do container — às vezes nem contém "kaggle".
// Resultado: o card do Kaggle ficava sem botão nenhum.

describe('sandboxKind', () => {
  it('reconhece Colab pelo nome', () => {
    expect(sandboxKind(host({ display_name: 'Colab 1 (CPU)' }))).toBe('colab');
    expect(sandboxKind(host({ host_id: '02151543' }))).toBe('colab');
  });

  it('reconhece Kaggle pelo nome do host', () => {
    expect(sandboxKind(host({ display_name: 'Kaggle GPU' }))).toBe('kaggle');
    expect(sandboxKind(host({ hostname: 'kaggle-runtime' }))).toBe('kaggle');
  });

  it('reconhece Kaggle pelo host_id (display_name é hash do container)', () => {
    // Caso real: o card mostrava "fc1027582ecb" antes de ser renomeado.
    expect(sandboxKind(host({ display_name: 'fc1027582ecb', hostname: 'fc1027582ecb' }))).toBe(
      null,
    );
    expect(
      sandboxKind(host({ display_name: 'fc1027582ecb', host_id: '558846ca-3973-40d1-ab7c-9dff20b97d5c' })),
    ).toBe('kaggle');
  });

  it('todos os host_ids de Kaggle conhecidos são reconhecidos', () => {
    for (const id of KAGGLE_HOST_IDS) {
      expect(sandboxKind(host({ display_name: 'x', host_id: id }))).toBe('kaggle');
    }
  });

  it('host comum não é sandbox (sem botão de ligar)', () => {
    expect(sandboxKind(host({ display_name: 'Macbook Air', hostname: 'Mac' }))).toBe(null);
    expect(sandboxKind(host({ display_name: 'Duck Server' }))).toBe(null);
    expect(sandboxKind(host({ display_name: 'Hetzner' }))).toBe(null);
  });
});

describe('sandboxLabel', () => {
  it('nomeia a plataforma pro texto do botão/link', () => {
    expect(sandboxLabel(host({ display_name: 'Kaggle GPU' }))).toBe('Kaggle');
    expect(sandboxLabel(host({ display_name: 'Colab 2 (CPU)' }))).toBe('Colab');
    expect(sandboxLabel(host({ display_name: 'Macbook' }))).toBe(null);
  });
});

describe('sandboxNotebookUrl', () => {
  it('Kaggle aponta pro notebook do kaggle.com, não pro Drive', () => {
    const url = sandboxNotebookUrl(host({ display_name: 'Kaggle GPU' }));
    expect(url).toContain('kaggle.com/code/');
    expect(url).not.toContain('colab.research.google.com');
  });

  it('Kaggle não leva authuser (parâmetro exclusivo do Colab)', () => {
    const url = sandboxNotebookUrl(host({ display_name: 'Kaggle', colab_authuser: '1' }));
    expect(url).not.toContain('authuser');
  });

  it('Kaggle respeita kaggle_notebook_url próprio, sem duplicar /edit', () => {
    const w = host({
      display_name: 'Kaggle',
      kaggle_notebook_url: 'https://www.kaggle.com/code/outro/notebook/edit',
    });
    const url = sandboxNotebookUrl(w)!;
    expect(url).toBe('https://www.kaggle.com/code/outro/notebook/edit');
    expect(url).not.toContain('/edit/edit');
  });

  it('Colab mantém a URL do Drive com authuser', () => {
    const url = sandboxNotebookUrl(
      host({ display_name: 'Colab 2 Pro (GPU T4)', colab_notebook_id: 'ABC123' }),
    )!;
    expect(url).toContain('colab.research.google.com/drive/ABC123');
    expect(url).toContain('authuser=1');
  });

  it('host comum não tem URL de notebook', () => {
    expect(sandboxNotebookUrl(host({ display_name: 'Macbook Air' }))).toBe(null);
  });
});
