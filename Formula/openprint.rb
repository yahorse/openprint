class Openprint < Formula
  desc "Open source printing protocol — driverless HTTP/REST printing"
  homepage "https://github.com/yahorse/openprint"
  url "https://github.com/yahorse/openprint/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "" # Updated by release workflow
  license "MIT"

  depends_on "python@3.13"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "OpenPrint", shell_output("#{bin}/opp --help")
  end
end
