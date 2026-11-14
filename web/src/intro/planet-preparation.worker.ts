import { buildPlanetaryPreparation, preparationTransfers, type PlanetPreparationReply, type PlanetPreparationRequest } from './planet-preparation';

const worker = self as unknown as {
  onmessage: ((event: MessageEvent<PlanetPreparationRequest>) => void) | null;
  postMessage(message: PlanetPreparationReply, transfer?: Transferable[]): void;
};
worker.onmessage = ({ data }) => {
  try {
    const planets = buildPlanetaryPreparation(data);
    worker.postMessage({ ok: true, planets }, preparationTransfers(planets));
  } catch {
    worker.postMessage({ ok: false });
  }
};
