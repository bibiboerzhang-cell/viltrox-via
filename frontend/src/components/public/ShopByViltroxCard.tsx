import { useEffect, useMemo, useState } from "react";

import { trackCreatorPublicClick } from "../../services/student.service";
import type { CreatorPublicPageData, ShopHero } from "../../types/api";
import { appendCreatorAttribution } from "../../utils/appendCreatorAttribution";

const FALLBACK_HERO: ShopHero = {
  id: "default-viltrox-shop",
  title: "Shop by Viltrox",
  subtitle: "Support the gear I use",
  imageUrl: "/mockups/viltrox-shop-vintage-z2.png",
  targetUrl: "https://viltrox.com/collections/all",
  badge: "Official Store",
  source: "manual",
};

interface Props {
  creator: CreatorPublicPageData["creator"];
  shopHeroes: ShopHero[];
}

export function ShopByViltroxCard({ creator, shopHeroes }: Props) {
  const heroes = useMemo(() => (shopHeroes.length ? shopHeroes : [FALLBACK_HERO]), [shopHeroes]);
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    if (heroes.length <= 1) return undefined;
    const timer = window.setInterval(() => {
      setActiveIndex((current) => (current + 1) % heroes.length);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [heroes.length]);

  const activeHero = heroes[activeIndex] || FALLBACK_HERO;
  const attributedUrl = appendCreatorAttribution(activeHero.targetUrl, creator.code);

  async function handleShopClick() {
    try {
      await trackCreatorPublicClick({
        creator_id: creator.id,
        creator_code: creator.code,
        type: "shop_click",
        target_url: attributedUrl,
        shop_hero_id: activeHero.id,
      });
    } catch (error) {
      console.warn("[creator-public] shop click tracking failed:", error);
    }
    window.open(attributedUrl, "_blank", "noopener,noreferrer");
  }

  return (
    <section
      className="creator-shop-card"
      style={{
        backgroundImage: `linear-gradient(90deg, rgba(0,0,0,.86) 0%, rgba(0,0,0,.64) 38%, rgba(0,0,0,.14) 72%), linear-gradient(180deg, rgba(0,0,0,.18), rgba(0,0,0,.42)), url("${activeHero.imageUrl}")`,
      }}
      aria-label="Shop by Viltrox"
    >
      <div className="creator-shop-card__copy">
        <small>Shop</small>
        <h2>{activeHero.title || "Shop by Viltrox"}</h2>
        <p>{activeHero.subtitle || "Support the gear I use"}</p>
        {activeHero.badge ? <span className="creator-shop-card__badge">{activeHero.badge}</span> : null}
        <button className="creator-shop-card__button" type="button" onClick={() => void handleShopClick()}>
          Shop Now →
        </button>
      </div>

      <div className="creator-shop-card__notes" aria-label="Shop assurances">
        <span>
          <strong>Official Store</strong>
          100% Authentic Gear
        </span>
        <span>
          <strong>Creator Attribution</strong>
          Support this creator
        </span>
        <span>
          <strong>Secure Checkout</strong>
          Safe & Reliable
        </span>
      </div>

      {heroes.length > 1 ? (
        <div className="creator-shop-card__dots" aria-label="Shop hero carousel">
          {heroes.map((hero, index) => (
            <button
              key={hero.id}
              type="button"
              className={index === activeIndex ? "is-active" : ""}
              aria-label={`Show shop hero ${index + 1}`}
              onClick={() => setActiveIndex(index)}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}
