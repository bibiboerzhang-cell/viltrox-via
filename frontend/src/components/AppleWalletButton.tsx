type AppleWalletButtonProps = {
  ready?: boolean;
  compact?: boolean;
  onClick: () => void;
};

export function AppleWalletButton({ ready = false, compact = false, onClick }: AppleWalletButtonProps) {
  return (
    <button
      className={`apple-wallet-button${compact ? " apple-wallet-button--compact" : ""}`}
      type="button"
      onClick={onClick}
      data-ready={ready ? "true" : "false"}
      aria-label="Add to Apple Wallet"
    >
      <span className="apple-wallet-button__icon" aria-hidden="true">
        <i />
      </span>
      <span className="apple-wallet-button__label">
        Add to
        <strong>Apple Wallet</strong>
      </span>
    </button>
  );
}
