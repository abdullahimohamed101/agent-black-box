import { Footer } from "../components/Footer";
import { Hero } from "../components/Hero";
import { TopBar } from "../components/TopBar";

export default function Page() {
  return (
    <>
      <a className="skip" href="#main">Skip to content</a>
      <TopBar />
      <main id="main">
        <Hero />
        {/* The sections of the brief are added here, in order, by the later steps. */}
        <section className="band" id="quickstart" aria-label="Run the demo" />
        <section className="band" id="how" aria-label="How it works" />
        <section className="band" id="screens" aria-label="Screens" />
      </main>
      <Footer />
    </>
  );
}
