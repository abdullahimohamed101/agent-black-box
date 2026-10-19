import fixture from "../../fixtures/migrate-billing-schema.json";
import { Architecture, Faq } from "../components/Architecture";
import { BeforeAfter } from "../components/BeforeAfter";
import { FaultTrace } from "../components/FaultTrace";
import { FinalCta } from "../components/FinalCta";
import { Footer } from "../components/Footer";
import { Hero } from "../components/Hero";
import { HowItWorks } from "../components/HowItWorks";
import { Privacy } from "../components/Privacy";
import { Quickstart } from "../components/Quickstart";
import { Records } from "../components/Records";
import { Screens } from "../components/Screens";
import { TopBar } from "../components/TopBar";
import { Watch } from "../components/Watch";
import { buildTrace, type Fixture } from "../lib/trace";

// Built once, on the server: nine rows and their inspector text, as plain data for the island.
const trace = buildTrace(fixture as unknown as Fixture);

// The order is the brief's section 3: hero, what a run holds, quickstart, how it works, watch (G1), screens, records, privacy,
// architecture, FAQ, final call to action, footer.
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
        <BeforeAfter />
        <Quickstart />
        <HowItWorks headline={trace.headline} />
        <Watch />
        <Screens />
        <Records />
        <Privacy />
        <Architecture />
        <Faq />
        <FinalCta />
      </main>
      <Footer />
    </>
  );
}
