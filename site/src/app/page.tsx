import fixture from "../../fixtures/migrate-billing-schema.json";
import { FaultTrace } from "../components/FaultTrace";
import { buildTrace, type Fixture } from "../lib/trace";
import { Footer } from "../components/Footer";
import { Hero } from "../components/Hero";
import { TopBar } from "../components/TopBar";

// Built once, on the server: nine rows and their inspector text, as plain data for the island.
const trace = buildTrace(fixture as unknown as Fixture);

export default function Page() {
  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <TopBar />
      <main id="main">
        <Hero>
          <FaultTrace model={trace} />
          <p className="fine">An illustrative failed run, drawn by this page from fixture events in the dashboard&apos;s own format.</p>
        </Hero>
        {/* The sections of the brief are added here, in order, by the later steps. */}
        <section className="band" id="quickstart" aria-label="Run the demo" />
        <section className="band" id="how" aria-label="How it works" />
        <section className="band" id="screens" aria-label="Screens" />
      </main>
      <Footer />
    </>
  );
}
