import { useEffect, useState } from "react";

type Health = {
  status: string;
  service: string;
};

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    const controller = new AbortController();

    fetch(`${apiBaseUrl}/health`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Health check failed");
        return response.json() as Promise<Health>;
      })
      .then(setHealth)
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setUnavailable(true);
      });

    return () => controller.abort();
  }, []);

  return (
    <main>
      <section>
        <p className="eyebrow">IT Support &amp; Operations</p>
        <h1>Agentic SupportOps</h1>
        <p className="summary">
          Production-oriented application baseline for incident management.
        </p>
        <div className="health" aria-live="polite">
          <span className={health ? "indicator online" : "indicator"} />
          {health
            ? `Backend online — ${health.service}`
            : unavailable
              ? "Backend unavailable"
              : "Checking backend health…"}
        </div>
      </section>
    </main>
  );
}

