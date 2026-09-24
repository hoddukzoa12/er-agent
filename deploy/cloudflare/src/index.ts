/**
 * Front Worker for the ER dispatch agent demo.
 *
 * Basic Auth keeps the public URL from spending our NIM and public-data quotas,
 * then every request (including NDJSON streams) goes to one container instance.
 */
import { Container, getContainer } from "@cloudflare/containers";
import { env } from "cloudflare:workers";

// Required by the SDK's outbound interception (allowedHosts / enableInternet=false).
export { ContainerProxy } from "@cloudflare/containers";

interface Env {
  ER_AGENT: DurableObjectNamespace<ErAgent>;
  NVIDIA_API_KEY: string;
  DATA_KEY: string;
  DEMO_PASSWORD: string;
  DEMO_USER?: string;
}

export class ErAgent extends Container<Env> {
  defaultPort = 8000;
  sleepAfter = "10m"; // billing stops once the container sleeps
  // Egress is open here: with @cloudflare/containers 0.3.7, `enableInternet = false` +
  // `allowedHosts` let plain HTTP through but HTTPS (NIM) and gRPC (Speech NIM) timed out.
  // Host-level egress control is enforced in the OpenShell deployment (deploy/openshell).
  enableInternet = true;
  envVars = {
    NVIDIA_API_KEY: (env as unknown as Env).NVIDIA_API_KEY,
    DATA_KEY: (env as unknown as Env).DATA_KEY,
  };
}

const encoder = new TextEncoder();

function sameSecret(a: string, b: string): boolean {
  const x = encoder.encode(a);
  const y = encoder.encode(b);
  return x.byteLength === y.byteLength && crypto.subtle.timingSafeEqual(x, y);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const expected = "Basic " + btoa(`${env.DEMO_USER ?? "judge"}:${env.DEMO_PASSWORD ?? ""}`);
    if (!env.DEMO_PASSWORD || !sameSecret(request.headers.get("Authorization") ?? "", expected)) {
      return new Response("Authentication required", {
        status: 401,
        headers: { "WWW-Authenticate": 'Basic realm="ER Agent demo", charset="UTF-8"' },
      });
    }
    // Bump the instance name when container start options (envVars, egress) change:
    // a running instance keeps the options it was started with.
    const stub = getContainer(env.ER_AGENT, "main-2");
    const retry = request.clone();
    const res = await stub.fetch(request);
    // After a rollout the Durable Object can still believe the old instance is healthy and
    // proxy into nothing. Reset it once and retry so the new image starts.
    if (res.status === 500 && (await res.clone().text()).includes("not running")) {
      await stub.destroy().catch(() => {});
      return stub.fetch(retry);
    }
    return res;
  },
};
