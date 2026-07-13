import { createConfig, http } from "wagmi";
import { mainnet } from "wagmi/chains";
import { isAddress } from "viem";

export const walletConfig = createConfig({
  chains: [mainnet],
  transports: {
    [mainnet.id]: http()
  },
  ssr: true
});

export function shortenAddress(address: string): string {
  if (address.length <= 12) return address;
  return address.slice(0, 6) + "..." + address.slice(-4);
}

export function normalizeWalletAddress(address: string): string {
  return isAddress(address) ? address : address;
}
