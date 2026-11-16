// Unit test for vcam_frames.cpp: scaling and BGRA -> YUY2 / NV12 / RGB32 (bottom-up) conversion.
// Built and run by scripts/build_native_win.ps1 -Test. Exit code 0 = pass.
#include <cstdio>
#include <cstdlib>
#include <vector>

#include "../vcam_frames.h"

static int fails = 0;
#define CHECK(c, ...) do { if (!(c)) { printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); ++fails; } } while (0)

int main() {
	const int W = 1920, H = 1080;
	std::vector<uint8_t> src((size_t)W * H * 4);
	for (int y = 0; y < H; ++y)
		for (int x = 0; x < W; ++x) {
			uint8_t *p = &src[((size_t)y * W + x) * 4];
			bool band = y < 120;                       // white band at the top, red elsewhere (BGRA)
			p[0] = band ? 255 : 0; p[1] = band ? 255 : 0; p[2] = band ? 255 : 200; p[3] = 255;
		}
	// scale 1920x1080 -> 1280x720: band rows 0..79
	std::vector<uint8_t> s((size_t)1280 * 720 * 4);
	vcam::scale_bgra(src.data(), W, H, s.data(), 1280, 720);
	CHECK(s[((size_t)40 * 1280 + 100) * 4 + 0] > 250, "scaled band row 40 B=%d", s[((size_t)40 * 1280 + 100) * 4]);
	CHECK(s[((size_t)400 * 1280 + 100) * 4 + 0] < 5 && s[((size_t)400 * 1280 + 100) * 4 + 2] > 195, "scaled red row 400");

	// RGB32 bottom-up: the band must be in the LAST rows of the buffer
	std::vector<uint8_t> rgb((size_t)W * H * 4);
	vcam::convert(src.data(), W, H, vcam::PixFmt::RGB32_BOTTOMUP, rgb.data(), W * 4);
	CHECK(rgb[((size_t)(H - 1 - 60) * W + 100) * 4] == 255, "rgb32 bottom-up band at buffer row %d", H - 1 - 60);
	CHECK(rgb[((size_t)60 * W + 100) * 4] == 0, "rgb32 buffer row 60 should be red");

	// YUY2: band luma ~235, red luma ~ 16 + 66*200/256 = 67
	std::vector<uint8_t> yuy((size_t)W * H * 2);
	vcam::convert(src.data(), W, H, vcam::PixFmt::YUY2, yuy.data(), W * 2);
	CHECK(yuy[(size_t)60 * W * 2 + 200] >= 230, "yuy2 band luma %d", yuy[(size_t)60 * W * 2 + 200]);
	CHECK(abs(yuy[(size_t)600 * W * 2 + 200] - 67) <= 2, "yuy2 red luma %d", yuy[(size_t)600 * W * 2 + 200]);

	// NV12
	std::vector<uint8_t> nv((size_t)W * H * 3 / 2);
	vcam::convert(src.data(), W, H, vcam::PixFmt::NV12, nv.data(), W);
	CHECK(nv[(size_t)60 * W + 100] >= 230 && abs(nv[(size_t)600 * W + 100] - 67) <= 2, "nv12 luma");
	uint8_t v = nv[(size_t)W * H + (size_t)300 * W + 101];
	CHECK(v > 200, "nv12 red V %d", v);

	// NV12 BT.709 (the Media Foundation camera): red (200,0,0) -> Y = 16 + 47*200/256 = 52.7
	std::vector<uint8_t> nv709((size_t)W * H * 3 / 2);
	vcam::convert(src.data(), W, H, vcam::PixFmt::NV12, nv709.data(), W, vcam::Matrix::BT709);
	CHECK(abs(nv709[(size_t)600 * W + 100] - 53) <= 2, "nv12 bt709 red luma %d", nv709[(size_t)600 * W + 100]);
	// decode the BT.709 chroma back: R = 1.164 (Y - 16) + 1.793 (V - 128) ~ 200
	{
		int Y = nv709[(size_t)600 * W + 100], V = nv709[(size_t)W * H + (size_t)300 * W + 101];
		double r = 1.164 * (Y - 16) + 1.793 * (V - 128);
		CHECK(r > 194 && r < 206, "nv12 bt709 red decodes to %.1f", r);
	}

	// waiting card: dark background, some light text pixels
	std::vector<uint8_t> card;
	vcam::waiting_card(1280, 720, card);
	int light = 0;
	for (size_t i = 0; i < card.size(); i += 4) light += card[i + 1] > 150;
	CHECK(card[0] == 40 && card[1] == 32 && card[2] == 28 && light > 500, "card bg %d,%d,%d light %d", card[0], card[1], card[2], light);

	printf(fails ? "%d FAILED\n" : "vcam_frames_test: all checks passed\n", fails);
	return fails ? 1 : 0;
}
