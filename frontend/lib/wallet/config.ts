import { isAddress } from "viem";

export function shortenAddress(address: string): string {
  if (address.length <= 12) return address;
  return address.slice(0, 6) + "..." + address.slice(-4);
}

export function normalizeWalletAddress(address: string): string | null {
  return isAddress(address) ? address : null;
}
