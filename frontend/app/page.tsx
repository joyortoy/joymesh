import { chatGPTSignInPath, getChatGPTUser } from "./chatgpt-auth";
import OnboardingClient from "./onboarding-client";

export const dynamic = "force-dynamic";

export default async function Home() {
  const user = await getChatGPTUser();

  if (!user) {
    return (
      <main className="signInShell">
        <section className="signInCard">
          <div className="brandMark" aria-hidden="true">J</div>
          <p className="eyebrow">JoyMesh control plane</p>
          <h1>Your coding harnesses.<br />One secure mesh.</h1>
          <p>
            Pair a JoyMesh Node, certify the tools already on your machine, and
            start signed remote tasks without exposing a local inbound port.
          </p>
          <a className="primaryButton" href={chatGPTSignInPath("/")}>
            Sign in to continue
          </a>
          <div className="trustLine">
            <span>Outbound-only node</span>
            <span>Explicit approvals</span>
            <span>Local secrets stay local</span>
          </div>
        </section>
      </main>
    );
  }

  return <OnboardingClient user={{ name: user.displayName, email: user.email }} />;
}
