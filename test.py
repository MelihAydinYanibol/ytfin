import requests
import json
import time

# API base URL
BASE_URL = "http://localhost:5000"

# Test video URL (short public domain video)
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=BCFAWkS_I8s"

def test_health():
    """Test health check endpoint"""
    print("\n=== Testing Health Check ===")
    try:
        response = requests.get(f"{BASE_URL}/health")
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")
        return response.status_code == 200
    except Exception as e:
        print(f"Error: {e}")
        return False


def test_get_resolutions(url):
    """Test GET resolutions endpoint"""
    print("\n=== Testing Get Resolutions ===")
    try:
        response = requests.get(f"{BASE_URL}/resolutions", params={"url": url})
        print(f"Status Code: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        
        if result.get('success'):
            return True, result.get('resolutions', [])
        return False, []
    except Exception as e:
        print(f"Error: {e}")
        return False, []


def test_get_fps(url):
    """Test GET fps endpoint"""
    print("\n=== Testing Get Available FPS ===")
    try:
        response = requests.get(f"{BASE_URL}/fps", params={"url": url})
        print(f"Status Code: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        
        if result.get('success'):
            return True, result.get('available_fps', [])
        return False, []
    except Exception as e:
        print(f"Error: {e}")
        return False, []


def test_download_video(url, resolution, fps=None, codec='h264'):
    """Test POST download endpoint"""
    fps_str = f" at {fps}fps" if fps else ""
    codec_str = f" ({codec})" if codec != 'h264' else ""
    print(f"\n=== Testing Download Video at {resolution}{fps_str}{codec_str} ===")
    try:
        payload = {
            "url": url,
            "resolution": resolution,
            "codec": codec
        }
        if fps:
            payload["fps"] = fps
            
        response = requests.post(
            f"{BASE_URL}/download",
            json=payload,
            headers={"Content-Type": "application/json"}
        )
        print(f"Status Code: {response.status_code}")
        result = response.json()
        print(f"Response: {json.dumps(result, indent=2)}")
        return result.get('success', False)
    except Exception as e:
        print(f"Error: {e}")
        return False
        return False


def main():
    """Run all tests"""
    print("Starting API Tests...")
    print(f"Testing video: {TEST_VIDEO_URL}")
    
    # Test health
    if not test_health():
        print("\n❌ Health check failed. Make sure the API is running!")
        print("Run: python main.py")
        return
    
    print("✓ Health check passed")
    
    # Test get resolutions
    success, resolutions = test_get_resolutions(TEST_VIDEO_URL)
    if not success:
        print("\n❌ Failed to get resolutions")
        return
    
    print(f"✓ Got resolutions: {resolutions}")
    
    # Test get fps
    success, fps_values = test_get_fps(TEST_VIDEO_URL)
    if not success:
        print("\n❌ Failed to get FPS values")
        return
    
    print(f"✓ Got available FPS: {fps_values}")
    
    # Test download with the highest resolution available
    if resolutions:
        highest_resolution = resolutions[0]  # First one is highest
        
        # Test H.264 without fps
        print(f"\n\nDownloading with {highest_resolution} resolution (H.264)...")
        if test_download_video(TEST_VIDEO_URL, highest_resolution, codec='h264'):
            print(f"✓ H.264 Download started successfully!")
            print("Check the 'downloads/' folder for the video file.")
        else:
            print(f"❌ H.264 Download failed")
        
        # Test HEVC without fps
        print(f"\n\nDownloading with {highest_resolution} resolution (HEVC)...")
        if test_download_video(TEST_VIDEO_URL, highest_resolution, codec='hevc'):
            print(f"✓ HEVC Download started successfully!")
            print("Check the 'downloads/' folder for the video file (smaller file size).")
        else:
            print(f"❌ HEVC Download failed (FFmpeg may not support libx265)")
        
        # Test with specific fps if available
        if fps_values:
            selected_fps = fps_values[0]  # Use highest available fps
            
            print(f"\n\nDownloading with {highest_resolution} resolution at {selected_fps}fps (HEVC)...")
            if test_download_video(TEST_VIDEO_URL, highest_resolution, fps=selected_fps, codec='hevc'):
                print(f"✓ HEVC Download with FPS conversion started successfully!")
                print("Check the 'downloads/' folder for the video file (MKV format).")
            else:
                print(f"❌ HEVC Download with FPS conversion failed (FFmpeg may not be installed)")
        else:
            print("\n⚠ No FPS information available from source")
    else:
        print("\n❌ No resolutions available to test download")


if __name__ == "__main__":
    main()
