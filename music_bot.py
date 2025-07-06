
import discord
import asyncio
from discord.ext import commands
import yt_dlp
import os
from dotenv import load_dotenv
import re
from urllib.parse import urlparse

# Bot ayarları
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents)

# YT-DLP ayarları
ydl_opts = {
    'format': 'bestaudio/best',
    'noplaylist': False,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': 'in_playlist',
    'geo_bypass': True,
    'socket_timeout': 10,
    'retries': 5,
    'fragment_retries': 5,
    'skip_unavailable_fragments': True,
    'cachedir': False,
    'extractor_args': {
        'youtube': {
            'player_client': ['mweb', 'android', 'web'],  # mweb client öncelikli
            'player_skip': 'configs',  # Bazı yapılandırmaları atla
        },
        'youtubetab': {
            'skip': 'webpage',  # Webpage isteklerini atla
        }
    },
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }]
}

# FFmpeg yolunu kontrol et
def check_ffmpeg():
    import subprocess
    try:
        # FFmpeg'i çalıştırmayı dene
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        print("FFmpeg PATH'de bulundu!")
        return 'ffmpeg'
    except (subprocess.SubprocessError, FileNotFoundError):
        print("FFmpeg PATH'de bulunamadı, alternatif yolları deniyorum...")
        
        # Olası FFmpeg yolları
        possible_paths = [
            'ffmpeg.exe',  # Aynı dizinde
            os.path.join(os.getcwd(), 'ffmpeg.exe'),  # Tam yol
            r'C:\ffmpeg\bin\ffmpeg.exe',  # Windows tipik yol
            '/usr/bin/ffmpeg',  # Linux tipik yol
            '/usr/local/bin/ffmpeg'  # macOS tipik yol
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                print(f"FFmpeg bulundu: {path}")
                return path
        
        print("FFmpeg bulunamadı! Lütfen FFmpeg'i yükleyin ve PATH'e ekleyin.")
        return 'ffmpeg'  # Yine de varsayılan değeri döndür

# FFmpeg yolunu belirle
FFMPEG_PATH = check_ffmpeg()

ffmpeg_options = {
    'options': '-vn',
    'executable': FFMPEG_PATH
}

class MusicPlayer:
    def __init__(self, bot):
        self.bot = bot
        self.queue = {}  # Sunucu başına sıra
        self.now_playing = {}  # Şu an çalan şarkı bilgisi
        self.text_channels = {}  # Sunucu başına son kullanılan metin kanalı
        self.control_messages = {}  # Kontrol mesajları
        self.search_results = {}  # Arama sonuçları
        self.leave_tasks = {}  # Otomatik ayrılma görevleri
        self.inactivity_timeout = 300  # 5 dakika (saniye cinsinden)
        
    # Mesaj gönderme yardımcı metodu
    async def send_message(self, ctx, content=None, embed=None, view=None):
        if isinstance(ctx, discord.Interaction):
            if not ctx.response.is_done():
                await ctx.response.defer(ephemeral=False)
            return await ctx.followup.send(content=content, embed=embed, view=view)
        else:
            return await ctx.send(content=content, embed=embed, view=view)

    # URL mi yoksa arama sorgusu mu kontrol et
    def is_url(self, url):
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except:
            return False

    # Arama sonuçlarını göster ve seçim yap
    async def show_search_results(self, ctx, search):
        guild_id = ctx.guild.id
        
        # Aramayı başlat
        if not hasattr(self, 'searching'):
            self.searching = {}
        self.searching[guild_id] = True
        print(f"Arama başlatıldı: {guild_id} - {search}")
        
        # Arama mesajı gönder
        if isinstance(ctx, discord.Interaction):
            if not ctx.response.is_done():
                await ctx.response.defer(ephemeral=False)
            searching_message = await ctx.followup.send(f"🔍 Aranıyor: `{search}`")
            self.text_channels[ctx.guild.id] = ctx.channel
        else:
            searching_message = await ctx.send(f"🔍 Aranıyor: `{search}`")
            self.text_channels[ctx.guild.id] = ctx.channel
        
        try:
            # Optimize edilmiş YT-DLP ayarları
            ydl_opts_search = {
                'format': 'bestaudio/best',
                'noplaylist': False,
                'nocheckcertificate': True,
                'quiet': True,
                'no_warnings': True,
                'default_search': 'auto',
                'extract_flat': 'in_playlist',
                'socket_timeout': 5,
                'skip_download': True,
                'cachedir': False,
            }
            
            # URL mi yoksa arama sorgusu mu kontrol et
            if self.is_url(search):
                # URL ise, doğrudan bilgileri al
                with yt_dlp.YoutubeDL(ydl_opts_search) as ydl:
                    info = ydl.extract_info(search, download=False, process=False)
                    
                    # Playlist mi kontrol et
                    if 'entries' in info:
                        # Playlist
                        playlist_title = info.get('title', 'Playlist')
                        entries = list(info['entries'])
                        
                        # Entries boş mu kontrol et
                        if not entries:
                            self.searching[guild_id] = False
                            await searching_message.delete()
                            if isinstance(ctx, discord.Interaction):
                                await ctx.followup.send(f"❌ Playlist boş veya erişilemez.")
                            else:
                                await ctx.send(f"❌ Playlist boş veya erişilemez.")
                            return None
                        
                        # Arama mesajını güncelle
                        await searching_message.edit(content=f"🎵 Playlist işleniyor: `{playlist_title}` ({len(entries)} şarkı)")
                        
                        # İlk şarkıyı çal, diğerlerini sıraya ekle
                        first_entry = entries[0]
                        
                        # İlk şarkı için detaylı bilgi al
                        first_song_info = ydl.extract_info(first_entry['url'], download=False)
                        
                        # İlk şarkıyı işle
                        song_info = await self.process_song_info(ctx, first_song_info, searching_message)
                        
                        # Diğer şarkıları sıraya ekle
                        if guild_id not in self.queue:
                            self.queue[guild_id] = []
                        
                        # Diğer şarkıları arka planda işle
                        if len(entries) > 1:
                            asyncio.create_task(self.process_playlist_entries(ctx, entries[1:], ydl))
                        
                        # Playlist bilgisi gönder
                        embed = discord.Embed(
                            title="🎵 Playlist Yüklendi",
                            description=f"**{playlist_title}**",
                            color=discord.Color.green()
                        )
                        
                        if song_info.get('thumbnail'):
                            embed.set_thumbnail(url=song_info['thumbnail'])
                        
                        embed.add_field(name="İlk Şarkı", value=song_info['title'], inline=True)
                        embed.add_field(name="Toplam Şarkı", value=str(len(entries)), inline=True)
                        embed.add_field(name="Sıraya Eklenen", value=str(len(entries) - 1), inline=True)
                        
                        if isinstance(ctx, discord.Interaction):
                            await ctx.followup.send(embed=embed)
                        else:
                            await ctx.send(embed=embed)
                        
                        # İlk şarkıyı çal
                        if not ctx.guild.voice_client or not ctx.guild.voice_client.is_playing():
                            await self.play_song(ctx, song_info)
                        
                        # Aramayı bitir
                        self.searching[guild_id] = False
                        return song_info
                    else:
                        # Tek şarkı
                        info = ydl.extract_info(search, download=False)
                        self.searching[guild_id] = False
                        song_info = await self.process_song_info(ctx, info, searching_message)
                        
                        # Şarkıyı çal
                        if not ctx.guild.voice_client or not ctx.guild.voice_client.is_playing():
                            await self.play_song(ctx, song_info)
                        
                        return song_info
            else:
                # Arama sorgusu ise, YouTube'da ara
                with yt_dlp.YoutubeDL(ydl_opts_search) as ydl:
                    info_dict = ydl.extract_info(f"ytsearch5:{search}", download=False, process=False)
                    results = list(info_dict.get('entries', []))
                    
                    # Sonuçları kontrol et
                    if not results:
                        self.searching[guild_id] = False
                        await searching_message.delete()
                        if isinstance(ctx, discord.Interaction):
                            await ctx.followup.send(f"❌ `{search}` için sonuç bulunamadı.")
                        else:
                            await ctx.send(f"❌ `{search}` için sonuç bulunamadı.")
                        return None
                    
                    # Arama mesajını sil
                    try:
                        await searching_message.delete()
                    except:
                        pass
                    
                    # Sonuçları göster
                    embed = discord.Embed(
                        title="🔍 Arama Sonuçları",
                        description=f"**{search}** için sonuçlar:",
                        color=discord.Color.blue()
                    )
                    
                    # Sonuçları listeye ekle
                    self.search_results[guild_id] = []
                    
                    for i, result in enumerate(results):
                        if not result:
                            continue
                            
                        title = result.get('title', 'Bilinmeyen Başlık')
                        uploader = result.get('uploader', 'Bilinmeyen Yükleyici')
                        duration = result.get('duration_string', 'Bilinmeyen Süre')
                        
                        embed.add_field(
                            name=f"{i+1}. {title}",
                            value=f"Yükleyen: {uploader} | Süre: {duration}",
                            inline=False
                        )
                        
                        # Sonucu listeye ekle
                        self.search_results[guild_id].append({
                            'title': title,
                            'url': '',
                            'thumbnail': result.get('thumbnail'),
                            'duration': result.get('duration'),
                            'webpage_url': f"https://www.youtube.com/watch?v={result['id']}",
                            'uploader': uploader
                        })
                    
                    # Hiç sonuç yoksa
                    if not self.search_results[guild_id]:
                        self.searching[guild_id] = False
                        if isinstance(ctx, discord.Interaction):
                            await ctx.followup.send(f"❌ `{search}` için sonuç bulunamadı.")
                        else:
                            await ctx.send(f"❌ `{search}` için sonuç bulunamadı.")
                        return None
                    
                    # Seçim için butonlar ekle
                    view = discord.ui.View()
                    for i in range(min(5, len(self.search_results[guild_id]))):
                        button = discord.ui.Button(label=str(i+1), style=discord.ButtonStyle.primary)
                        button.callback = self.create_select_callback(ctx, i)
                        view.add_item(button)
                    
                    # İptal butonu
                    cancel_button = discord.ui.Button(label="İptal", style=discord.ButtonStyle.danger)
                    cancel_button.callback = self.create_cancel_callback(ctx)
                    view.add_item(cancel_button)
                    
                    # Sonuçları gönder
                    if isinstance(ctx, discord.Interaction):
                        await ctx.followup.send(embed=embed, view=view)
                    else:
                        await ctx.send(embed=embed, view=view)
                    
                    # Aramayı bitir
                    self.searching[guild_id] = False
                    print(f"Arama tamamlandı: {guild_id}")
                    return None  # Henüz şarkı seçilmedi
        except Exception as e:
            self.searching[guild_id] = False
            print(f"Arama hatası: {e}")
            
            # Arama mesajını sil
            try:
                await searching_message.delete()
            except:
                pass
            
            # Hata mesajı gönder
            if isinstance(ctx, discord.Interaction):
                await ctx.followup.send(f"Arama sırasında bir hata oluştu: {e}")
            else:
                await ctx.send(f"Arama sırasında bir hata oluştu: {e}")
            return None

    # Seçim butonu callback'i oluştur
    def create_select_callback(self, ctx, index):
        async def select_callback(interaction):
            guild_id = interaction.guild.id
            if guild_id in self.search_results and index < len(self.search_results[guild_id]):
                selected_song = self.search_results[guild_id][index]
                print(f"Şarkı seçildi: {selected_song['title']}")
                
                # Mesajı güncelle
                await interaction.response.edit_message(
                    content=f"🎵 **{selected_song['title']}** seçildi!",
                    embed=None,
                    view=None
                )
                
                # Yükleniyor mesajı
                loading_message = await interaction.followup.send("🔄 Şarkı yükleniyor, lütfen bekleyin...")
                
                try:
                    # Şarkı URL'sini al
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(selected_song['webpage_url'], download=False)
                        selected_song['url'] = info.get('url', '')
                    
                    # Yükleniyor mesajını sil
                    await loading_message.delete()
                    
                    # Şarkıyı çal veya sıraya ekle
                    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
                        # Sıraya ekle
                        if guild_id not in self.queue:
                            self.queue[guild_id] = []
                        
                        self.queue[guild_id].append(selected_song)
                        print(f"Şarkı sıraya eklendi: {selected_song['title']}")
                        
                        # Sıraya eklendiğini bildir
                        embed = discord.Embed(
                            title="🎵 Sıraya Eklendi",
                            description=f"**{selected_song['title']}**",
                            color=discord.Color.green()
                        )
                        
                        if selected_song.get('thumbnail'):
                            embed.set_thumbnail(url=selected_song['thumbnail'])
                        
                        embed.add_field(name="Sıra Pozisyonu", value=f"#{len(self.queue[guild_id])}", inline=True)
                        
                        await interaction.followup.send(embed=embed)
                    else:
                        # Doğrudan çal
                        print(f"Şarkı doğrudan çalınıyor: {selected_song['title']}")
                        await self.play_song(interaction, selected_song)
                except Exception as e:
                    # Yükleniyor mesajını sil
                    try:
                        await loading_message.delete()
                    except:
                        pass
                    
                    print(f"Şarkı yükleme hatası: {e}")
                    await interaction.followup.send(f"Şarkı yüklenirken bir hata oluştu: {e}")
            else:
                print(f"Şarkı seçim hatası: {guild_id} - {index}")
                await interaction.response.send_message("Seçim yapılamadı. Lütfen tekrar deneyin.", ephemeral=True)
        
        return select_callback
    
    # İptal butonu callback'i oluştur
    def create_cancel_callback(self, ctx):
        async def cancel_callback(interaction):
            await interaction.response.edit_message(
                content="❌ Arama iptal edildi.",
                embed=None,
                view=None
            )
        return cancel_callback
    
    # Şarkı bilgilerini işle
    async def process_song_info(self, ctx, info, searching_message=None):
        try:
            # Arama mesajını sil
            if searching_message:
                try:
                    await searching_message.delete()
                except:
                    pass
            
            # Aramayı bitir
            if hasattr(self, 'searching'):
                guild_id = ctx.guild.id
                self.searching[guild_id] = False
            
            # Şarkı bilgilerini içeren bir embed oluştur
            embed = discord.Embed(
                title="🎵 Şarkı Bilgileri",
                description=f"**{info['title']}**",
                color=discord.Color.green()
            )
            
            if info.get('thumbnail'):
                embed.set_thumbnail(url=info['thumbnail'])
            
            embed.add_field(name="Yükleyen", value=info.get('uploader', 'Bilinmeyen'), inline=True)
            
            if info.get('duration'):
                duration = info['duration']
                if isinstance(duration, (int, float)):
                    minutes, seconds = divmod(int(duration), 60)
                    embed.add_field(name="Süre", value=f"{minutes}:{seconds:02d}", inline=True)
            
            embed.add_field(name="Kaynak", value=f"[Link]({info.get('webpage_url', '')})", inline=True)
            
            # Şarkı bilgilerini döndür
            song_info = {
                'title': info['title'],
                'url': info.get('url', ''),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'webpage_url': info.get('webpage_url', ''),
                'uploader': info.get('uploader', 'Bilinmeyen')
            }
            
            return song_info
        except Exception as e:
            print(f"Şarkı bilgisi işleme hatası: {e}")
            raise e

    # Şarkı çal
    async def play_song(self, ctx, song_info):
        guild_id = ctx.guild.id
        
        # Ses kanalına bağlan
        if not ctx.guild.voice_client:
            # Kullanıcının ses kanalını bul
            if isinstance(ctx, discord.Interaction):
                voice_channel = ctx.user.voice.channel
            else:
                voice_channel = ctx.author.voice.channel
            
            # Ses kanalına bağlan
            try:
                voice_client = await voice_channel.connect()
                print(f"Ses kanalına bağlandı: {voice_channel.name}")
            except Exception as e:
                print(f"Ses kanalına bağlanma hatası: {e}")
                if isinstance(ctx, discord.Interaction):
                    await ctx.followup.send(f"Ses kanalına bağlanırken bir hata oluştu: {e}")
                else:
                    await ctx.send(f"Ses kanalına bağlanırken bir hata oluştu: {e}")
                return
        else:
            voice_client = ctx.guild.voice_client
        
        # Şarkı URL'sini kontrol et
        try:
            song_info = await self.get_song_url(song_info)
        except Exception as e:
            print(f"URL alma hatası: {e}")
            error_msg = str(e)
            if "Sign in to confirm you're not a bot" in error_msg:
                error_msg = "YouTube bot koruması nedeniyle bu şarkı çalınamıyor. Lütfen başka bir şarkı deneyin veya birkaç dakika sonra tekrar deneyin."
            elif "This content isn't available" in error_msg:
                error_msg = "YouTube istek limiti aşıldı. Lütfen birkaç dakika bekleyip tekrar deneyin."
            elif "PO Token" in error_msg:
                error_msg = "YouTube PO Token gerekiyor. Bot yöneticisiyle iletişime geçin."
            
            if isinstance(ctx, discord.Interaction):
                await ctx.followup.send(f"Şarkı yüklenirken bir hata oluştu: {error_msg}")
            else:
                await ctx.send(f"Şarkı yüklenirken bir hata oluştu: {error_msg}")
            return
        
        # Ses kaynağını oluştur
        try:
            audio_source = discord.FFmpegPCMAudio(song_info['url'], **ffmpeg_options)
            audio_source = discord.PCMVolumeTransformer(audio_source, volume=0.5)
        except Exception as e:
            print(f"Ses kaynağı oluşturma hatası: {e}")
            if isinstance(ctx, discord.Interaction):
                await ctx.followup.send(f"Ses kaynağı oluşturulurken bir hata oluştu: {e}")
            else:
                await ctx.send(f"Ses kaynağı oluşturulurken bir hata oluştu: {e}")
            return
        
        # Eğer zaten çalıyorsa, durdur
        if voice_client.is_playing():
            voice_client.stop()
        
        # Şu an çalan şarkı bilgisini güncelle
        self.now_playing[guild_id] = song_info
        
        # Şarkıyı çal
        def after_playing(error):
            if error:
                print(f"Oynatma hatası: {error}")
            
            # Bot hala bağlı mı kontrol et
            if self.bot.get_guild(guild_id) and self.bot.get_guild(guild_id).voice_client:
                # Bir sonraki şarkıyı çal
                coro = self.play_next(guild_id)
                fut = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
                try:
                    fut.result()
                except Exception as e:
                    print(f"play_next hatası: {e}")
        
        voice_client.play(audio_source, after=after_playing)
        print(f"Şarkı çalmaya başladı: {song_info['title']}")
        
        # Kontrol arayüzü oluştur
        await self.create_control_panel(ctx, song_info)

    # Sıradaki şarkıyı çal
    async def play_next(self, guild_id):
        print(f"play_next çağrıldı: {guild_id}")
        
        # Sunucuyu ve ses istemcisini kontrol et
        guild = self.bot.get_guild(guild_id)
        if not guild:
            print(f"Guild bulunamadı: {guild_id}")
            return
        
        voice_client = guild.voice_client
        if not voice_client or not voice_client.is_connected():
            print(f"Ses istemcisi bağlı değil: {guild_id}")
            return
        
        # Sırada şarkı var mı kontrol et
        if guild_id in self.queue and self.queue[guild_id]:
            # Eğer varsa, önceki ayrılma görevini iptal et
            if guild_id in self.leave_tasks and not self.leave_tasks[guild_id].done():
                self.leave_tasks[guild_id].cancel()
                print(f"Ayrılma görevi iptal edildi: {guild_id}")
            
            next_song = self.queue[guild_id].pop(0)
            print(f"Sıradaki şarkı: {next_song['title']}")
            
            # URL'yi kontrol et ve gerekirse yeniden al
            try:
                next_song = await self.get_song_url(next_song)
            except Exception as e:
                print(f"URL yeniden alma hatası: {e}")
                # Metin kanalını bul ve hata mesajı gönder
                if guild_id in self.text_channels:
                    channel = self.text_channels[guild_id]
                    await channel.send(f"Şarkı URL'si alınamadı: {e}")
                # Bir sonraki şarkıya geç
                return await self.play_next(guild_id)
            
            # Ses kaynağını oluştur
            try:
                print(f"Sıradaki şarkı URL'si: {next_song['url']}")
                
                # FFmpeg seçeneklerini ayarla
                ffmpeg_before_options = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
                
                # Ses kaynağını oluştur
                audio_source = discord.FFmpegPCMAudio(
                    next_song['url'],
                    before_options=ffmpeg_before_options,
                    options='-vn',
                    executable=FFMPEG_PATH
                )
                
                # Ses seviyesini ayarla
                audio_source = discord.PCMVolumeTransformer(audio_source, volume=0.5)
                
            except Exception as e:
                print(f"FFmpeg hatası: {e}")
                # Metin kanalını bul ve hata mesajı gönder
                if guild_id in self.text_channels:
                    channel = self.text_channels[guild_id]
                    await channel.send(f"Ses kaynağı oluşturulamadı: {e}")
                # Bir sonraki şarkıya geç
                return await self.play_next(guild_id)
            
            # Şu an çalan şarkı bilgisini güncelle
            self.now_playing[guild_id] = next_song
            
            # Eğer zaten çalıyorsa, durdur
            if voice_client.is_playing():
                voice_client.stop()
            
            # Şarkıyı çal
            def after_playing(error):
                if error:
                    print(f"Oynatma hatası: {error}")
                
                # Bot hala bağlı mı kontrol et
                if self.bot.get_guild(guild_id) and self.bot.get_guild(guild_id).voice_client:
                    # Bir sonraki şarkıyı çal
                    coro = self.play_next(guild_id)
                    fut = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
                    try:
                        fut.result()
                    except Exception as e:
                        print(f"play_next hatası: {e}")
            
            voice_client.play(audio_source, after=after_playing)
            print(f"Şarkı çalmaya başladı: {next_song['title']}")
            
            # Eski kontrol panelini güncelle
            if guild_id in self.control_messages:
                try:
                    # Metin kanalını bul
                    channel = self.text_channels[guild_id]
                    
                    # Yeni kontrol paneli oluştur
                    await self.create_control_panel(channel, next_song, update=True)
                except Exception as e:
                    print(f"Kontrol paneli güncelleme hatası: {e}")
        else:
            # Sırada şarkı yoksa
            print(f"Sırada şarkı yok: {guild_id}")
            
            # Şu an çalan şarkı bilgisini temizle
            self.now_playing.pop(guild_id, None)
            
            # Kontrol mesajını güncelle
            if guild_id in self.control_messages:
                try:
                    control_message = self.control_messages[guild_id]
                    await control_message.edit(content="✅ Tüm şarkılar tamamlandı!", embed=None, view=None)
                    self.control_messages.pop(guild_id, None)
                except Exception as e:
                    print(f"Kontrol mesajı temizleme hatası: {e}")
            
            # Otomatik ayrılma görevi oluştur
            async def leave_after_timeout():
                try:
                    await asyncio.sleep(self.inactivity_timeout)  # 5 dakika bekle
                    
                    # Hala bağlı mı kontrol et
                    if guild.voice_client and guild.voice_client.is_connected():
                        # Metin kanalına bilgi mesajı gönder
                        if guild_id in self.text_channels:
                            channel = self.text_channels[guild_id]
                            await channel.send("👋 5 dakika boyunca kullanılmadığı için ses kanalından ayrılıyorum.")
                        
                        # Ses kanalından ayrıl
                        await guild.voice_client.disconnect()
                        print(f"İnaktivite nedeniyle ses kanalından ayrıldı: {guild_id}")
                except asyncio.CancelledError:
                    # Görev iptal edildi
                    pass
                except Exception as e:
                    print(f"Otomatik ayrılma hatası: {e}")
            
            # Önceki görevi iptal et (eğer varsa)
            if guild_id in self.leave_tasks and not self.leave_tasks[guild_id].done():
                self.leave_tasks[guild_id].cancel()
            
            # Yeni görevi oluştur ve başlat
            self.leave_tasks[guild_id] = asyncio.create_task(leave_after_timeout())
            print(f"Otomatik ayrılma görevi oluşturuldu: {guild_id}, {self.inactivity_timeout} saniye sonra")

    # Kontrol arayüzü oluştur
    async def create_control_panel(self, ctx, song_info, update=False):
        guild_id = ctx.guild.id
        
        # Embed oluştur
        embed = discord.Embed(
            title="🎵 Şu an çalıyor",
            description=f"**{song_info['title']}**",
            color=discord.Color.blue()
        )
        
        if song_info.get('thumbnail'):
            embed.set_thumbnail(url=song_info['thumbnail'])
        
        embed.add_field(name="Yükleyen", value=song_info.get('uploader', 'Bilinmeyen'), inline=True)
        
        if song_info.get('duration'):
            duration = song_info['duration']
            if isinstance(duration, (int, float)):
                minutes, seconds = divmod(int(duration), 60)
                embed.add_field(name="Süre", value=f"{minutes}:{seconds:02d}", inline=True)
        
        embed.add_field(name="Kaynak", value=f"[Link]({song_info.get('webpage_url', '')})", inline=True)
        
        # Kontrol butonları
        view = discord.ui.View()
        
        # Duraklat/Devam Et butonu
        pause_button = discord.ui.Button(style=discord.ButtonStyle.primary, label="⏯️ Duraklat/Devam Et")
        pause_button.callback = self.create_pause_callback(ctx=None)
        view.add_item(pause_button)
        
        # Geç butonu
        skip_button = discord.ui.Button(style=discord.ButtonStyle.primary, label="⏭️ Geç")
        skip_button.callback = self.create_skip_callback(ctx=None)
        view.add_item(skip_button)
        
        # Sıra butonu
        queue_button = discord.ui.Button(style=discord.ButtonStyle.primary, label="📋 Sıra")
        queue_button.callback = self.create_queue_callback(ctx=None)
        view.add_item(queue_button)
        
        # Durdur butonu
        stop_button = discord.ui.Button(style=discord.ButtonStyle.danger, label="⏹️ Durdur")
        stop_button.callback = self.create_stop_callback(ctx=None)
        view.add_item(stop_button)
        
        # Ayrıl butonu
        leave_button = discord.ui.Button(style=discord.ButtonStyle.danger, label="👋 Ayrıl")
        leave_button.callback = self.create_leave_callback(ctx=None)
        view.add_item(leave_button)
        
        # Metin kanalını belirle
        if isinstance(ctx, discord.Interaction):
            channel = ctx.channel
        elif isinstance(ctx, commands.Context):
            channel = ctx.channel
        else:
            channel = ctx  # Doğrudan kanal nesnesi verilmiş
        
        # Kontrol mesajını gönder veya güncelle
        if update and guild_id in self.control_messages:
            try:
                # Mevcut kontrol mesajını güncelle
                message = self.control_messages[guild_id]
                await message.edit(embed=embed, view=view)
                print(f"Kontrol paneli güncellendi: {guild_id}")
            except Exception as e:
                print(f"Kontrol paneli güncelleme hatası: {e}")
                # Güncelleme başarısız olursa yeni mesaj gönder
                message = await channel.send(embed=embed, view=view)
                self.control_messages[guild_id] = message
                print(f"Yeni kontrol paneli oluşturuldu: {guild_id}")
        else:
            # Yeni kontrol mesajı gönder
            message = await channel.send(embed=embed, view=view)
            self.control_messages[guild_id] = message
            print(f"Kontrol paneli oluşturuldu: {guild_id}")

    # Duraklat/Devam Et butonu callback'i
    def create_pause_callback(self, ctx):
        async def pause_callback(interaction):
            guild_id = interaction.guild.id
            
            if interaction.guild.voice_client:
                if interaction.guild.voice_client.is_playing():
                    interaction.guild.voice_client.pause()
                    await interaction.response.send_message("⏸️ Müzik duraklatıldı.", ephemeral=True)
                elif interaction.guild.voice_client.is_paused():
                    interaction.guild.voice_client.resume()
                    await interaction.response.send_message("▶️ Müzik devam ediyor.", ephemeral=True)
                else:
                    await interaction.response.send_message("Şu anda çalan bir müzik yok.", ephemeral=True)
            else:
                await interaction.response.send_message("Bot bir ses kanalında değil.", ephemeral=True)
        
        return pause_callback
    
    # Geç butonu callback'i
    def create_skip_callback(self, ctx):
        async def skip_callback(interaction):
            guild_id = interaction.guild.id
            
            if interaction.guild.voice_client and (interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused()):
                interaction.guild.voice_client.stop()  # Şu anki şarkıyı durdur, play_next otomatik olarak çağrılacak
                await interaction.response.send_message("⏭️ Şarkı geçildi.", ephemeral=True)
            else:
                await interaction.response.send_message("Şu anda çalan bir müzik yok.", ephemeral=True)
        
        return skip_callback
    
    # Durdur butonu callback'i
    def create_stop_callback(self, ctx):
        async def stop_callback(interaction):
            guild_id = interaction.guild.id
            
            if interaction.guild.voice_client:
                if interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused():
                    # Sırayı temizle
                    if guild_id in self.queue:
                        self.queue[guild_id] = []
                    
                    # Şu an çalan şarkı bilgisini temizle
                    self.now_playing.pop(guild_id, None)
                    
                    # Müziği durdur
                    interaction.guild.voice_client.stop()
                    await interaction.response.send_message("⏹️ Müzik durduruldu ve sıra temizlendi!", ephemeral=True)
                else:
                    await interaction.response.send_message("Şu anda çalan bir müzik yok.", ephemeral=True)
            else:
                await interaction.response.send_message("Bot bir ses kanalında değil.", ephemeral=True)
        
        return stop_callback
    
    # Sıra butonu callback'i
    def create_queue_callback(self, ctx):
        async def queue_callback(interaction):
            guild_id = interaction.guild.id
            
            if guild_id not in self.queue or not self.queue[guild_id]:
                await interaction.response.send_message("Sırada şarkı yok.", ephemeral=True)
                return
            
            # Sıra embed'i oluştur
            embed = discord.Embed(
                title="🎵 Şarkı Sırası",
                color=discord.Color.blue()
            )
            
            # Şu an çalan şarkı
            if guild_id in self.now_playing:
                now_playing = self.now_playing[guild_id]
                embed.add_field(
                    name="Şu an oynatılıyor:",
                    value=f"**{now_playing['title']}**",
                    inline=False
                )
            
            # Sıradaki şarkılar
            queue_text = ""
            for i, song in enumerate(self.queue[guild_id]):
                queue_text += f"{i+1}. **{song['title']}**\n"
                
                # Çok uzunsa kısalt
                if i >= 9:  # İlk 10 şarkıyı göster
                    remaining = len(self.queue[guild_id]) - 10
                    queue_text += f"... ve {remaining} şarkı daha"
                    break
                    
            embed.add_field(name="Sıradaki şarkılar:", value=queue_text, inline=False)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        return queue_callback
    
    # Ayrıl butonu callback'i
    def create_leave_callback(self, ctx):
        async def leave_callback(interaction):
            guild_id = interaction.guild.id
            
            if interaction.guild.voice_client:
                # Sırayı temizle
                if guild_id in self.queue:
                    self.queue[guild_id] = []
                
                # Şu an çalan şarkı bilgisini temizle
                self.now_playing.pop(guild_id, None)
                
                # Kanaldan ayrıl
                await interaction.guild.voice_client.disconnect()
                await interaction.response.send_message("👋 Ses kanalından ayrıldım.", ephemeral=True)
            else:
                await interaction.response.send_message("Zaten bir ses kanalında değilim.", ephemeral=True)
        
        return leave_callback

    # URL'yi kontrol et ve gerekirse yeniden al
    async def get_song_url(self, song_info):
        if not song_info.get('url') or song_info['url'] == '':
            print(f"URL bulunamadı, yeniden alınıyor: {song_info['title']}")
            try:
                # Optimize edilmiş YT-DLP ayarları
                ydl_opts_url = {
                    'format': 'bestaudio/best',
                    'noplaylist': True,
                    'nocheckcertificate': True,
                    'quiet': True,
                    'no_warnings': True,
                    'socket_timeout': 10,
                    'skip_download': True,
                    'cachedir': False,
                    'geo_bypass': True,
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['mweb', 'android', 'web'],  # mweb client öncelikli
                            'player_skip': 'configs',  # Bazı yapılandırmaları atla
                        },
                        'youtubetab': {
                            'skip': 'webpage',  # Webpage isteklerini atla
                        }
                    },
                }
                
                with yt_dlp.YoutubeDL(ydl_opts_url) as ydl:
                    # URL'yi yeniden al
                    info = ydl.extract_info(song_info['webpage_url'], download=False)
                    song_info['url'] = info.get('url', '')
                    return song_info
            except Exception as e:
                print(f"URL yeniden alma hatası: {e}")
                # Alternatif kaynak dene
                try:
                    # Farklı bir format dene
                    ydl_opts_alt = {
                        'format': 'worstaudio/worst',  # Daha düşük kalite dene
                        'noplaylist': True,
                        'quiet': True,
                        'geo_bypass': True,
                        'skip_download': True,
                        'sleep_interval': 5,  # İstekler arasında 5 saniye bekle
                        'max_sleep_interval': 10,  # Maksimum 10 saniye bekle
                        'extractor_args': {
                            'youtube': {
                                'player_client': ['tv_embedded', 'mweb', 'android'],  # Farklı istemciler dene
                            }
                        }
                    }
                    with yt_dlp.YoutubeDL(ydl_opts_alt) as ydl:
                        info = ydl.extract_info(song_info['webpage_url'], download=False)
                        song_info['url'] = info.get('url', '')
                        return song_info
                except Exception as e2:
                    print(f"Alternatif kaynak denemesi başarısız: {e2}")
                    # Son çare olarak doğrudan URL oluşturmayı dene
                    try:
                        # YouTube video ID'sini al
                        video_id = None
                        if 'youtube.com' in song_info['webpage_url'] or 'youtu.be' in song_info['webpage_url']:
                            import re
                            patterns = [
                                r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
                                r'(?:embed\/|v\/|youtu.be\/)([0-9A-Za-z_-]{11})'
                            ]
                            for pattern in patterns:
                                match = re.search(pattern, song_info['webpage_url'])
                                if match:
                                    video_id = match.group(1)
                                    break
                        
                        if video_id:
                            # Alternatif bir müzik servisi kullan
                            print(f"YouTube kısıtlaması nedeniyle alternatif servis deneniyor: {video_id}")
                            # Burada alternatif bir müzik servisi API'si kullanabilirsiniz
                            # Örnek olarak, YouTube Music API veya başka bir servis
                            
                            # Şimdilik basit bir hata mesajı döndürelim
                            raise Exception("YouTube kısıtlaması nedeniyle bu video şu anda oynatılamıyor. Lütfen başka bir şarkı deneyin.")
                    except:
                        raise e
        return song_info

    # Playlist şarkılarını arka planda işle
    async def process_playlist_entries(self, ctx, entries, ydl):
        guild_id = ctx.guild.id
        
        # entries bir islice nesnesi olabilir, listeye dönüştür
        entries_list = list(entries)
        processed_count = 0
        
        for entry in entries_list:
            try:
                if not entry:
                    continue
                
                # Her şarkı için detaylı bilgi al
                detailed_info = ydl.extract_info(entry['url'], download=False, process=False)
                
                if not detailed_info:
                    continue
                
                self.queue[guild_id].append({
                    'title': detailed_info.get('title', 'Bilinmeyen Başlık'),
                    'url': detailed_info.get('url', ''),
                    'thumbnail': detailed_info.get('thumbnail'),
                    'duration': detailed_info.get('duration'),
                    'webpage_url': detailed_info.get('webpage_url', ''),
                    'uploader': detailed_info.get('uploader', 'Bilinmeyen Yükleyici')
                })
                processed_count += 1
            except Exception as e:
                print(f"Playlist şarkı bilgisi alma hatası: {e}")
        
        # Playlist bilgisi gönder
        if processed_count > 0 and guild_id in self.text_channels:
            channel = self.text_channels[guild_id]
            embed = discord.Embed(
                title="🎵 Playlist Sıraya Eklendi",
                description=f"**{processed_count} şarkı sıraya eklendi**",
                color=discord.Color.green()
            )
            
            await channel.send(embed=embed)

# Müzik oynatıcısını oluştur
music_player = MusicPlayer(bot)

@bot.event
async def on_ready():
    print(f'{bot.user.name} olarak giriş yapıldı!')
    
    # Tüm ses kanallarından ayrıl
    for guild in bot.guilds:
        if guild.voice_client:
            await guild.voice_client.disconnect(force=True)
            print(f"{guild.name} sunucusundaki ses kanalından ayrıldım.")
    
    # FFmpeg kontrolü
    import subprocess
    try:
        result = subprocess.run([FFMPEG_PATH, '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print(f"FFmpeg sürümü: {result.stdout.split('version')[1].split(' ')[1]}")
    except Exception as e:
        print(f"FFmpeg kontrolü başarısız: {e}")
        print("Lütfen FFmpeg'i yükleyin ve doğru yolu belirtin!")
    
    print('Bot hazır!')
    
    # Slash komutlarını kaydet
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} slash komut senkronize edildi.")
    except Exception as e:
        print(f"Slash komutları senkronize edilirken hata oluştu: {e}")
    
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="!help"))

@bot.command(name='play', help='YouTube\'dan müzik çalar (URL veya şarkı adı)')
async def play(ctx, *, query):
    # Kullanıcı ses kanalında mı kontrol et
    if not ctx.author.voice:
        return await ctx.send("Bir ses kanalında değilsiniz!")
    
    # Yükleniyor mesajı gönder
    loading_message = await ctx.send("🔄 Şarkı yükleniyor, lütfen bekleyin...")
    
    try:
        # Önce şarkı bilgilerini al
        song_info = await music_player.show_search_results(ctx, query)
        
        # Yükleniyor mesajını sil
        await loading_message.delete()
        
        # Eğer doğrudan URL girildiyse ve song_info döndüyse
        if song_info:
            guild_id = ctx.guild.id
            
            # Bot zaten bağlı mı ve çalıyor mu kontrol et
            if ctx.voice_client and ctx.voice_client.is_playing():
                # Sıraya ekle
                if guild_id not in music_player.queue:
                    music_player.queue[guild_id] = []
                    
                music_player.queue[guild_id].append(song_info)
                
                # Sıraya eklendiğini bildir
                embed = discord.Embed(
                    title="🎵 Sıraya Eklendi",
                    description=f"**{song_info['title']}**",
                    color=discord.Color.green()
                )
                
                if song_info.get('thumbnail'):
                    embed.set_thumbnail(url=song_info['thumbnail'])
                    
                embed.add_field(name="Sıra Pozisyonu", value=f"#{len(music_player.queue[guild_id])}", inline=True)
                
                await ctx.send(embed=embed)
            else:
                # Doğrudan çal (ses kanalına bağlanma işlemi play_song içinde yapılacak)
                await music_player.play_song(ctx, song_info)
                
    except Exception as e:
        # Yükleniyor mesajını sil
        try:
            await loading_message.delete()
        except:
            pass
        
        await ctx.send(f"Bir hata oluştu: {str(e)}")
        print(f"Genel Hata: {str(e)}")

@bot.command(name='pause', help='Müziği duraklatır')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Müzik duraklatıldı.")
    else:
        await ctx.send("Şu anda çalan bir müzik yok.")

@bot.command(name='resume', help='Duraklatılmış müziği devam ettirir')
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Müzik devam ediyor.")
    else:
        await ctx.send("Duraklatılmış bir müzik yok.")

@bot.command(name='stop', help='Müziği durdurur')
async def stop(ctx):
    if ctx.voice_client:
        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            # Sırayı temizle
            guild_id = ctx.guild.id
            if guild_id in music_player.queue:
                music_player.queue[guild_id] = []
                
            # Şu an çalan şarkı bilgisini temizle
            music_player.now_playing.pop(guild_id, None)
            
            # Müziği durdur
            ctx.voice_client.stop()
            await ctx.send("⏹️ Müzik durduruldu ve sıra temizlendi!")
        else:
            await ctx.send("Şu anda çalan bir müzik yok.")
    else:
        await ctx.send("Bot bir ses kanalında değil.")

@bot.command(name='skip', help='Sıradaki şarkıya geçer')
async def skip(ctx):
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        ctx.voice_client.stop()  # Şu anki şarkıyı durdur, play_next otomatik olarak çağrılacak
        await ctx.send("⏭️ Şarkı geçildi.")
    else:
        await ctx.send("Şu anda çalan bir müzik yok.")

@bot.command(name='queue', help='Şarkı sırasını gösterir')
async def queue(ctx):
    guild_id = ctx.guild.id
    
    if guild_id not in music_player.queue or not music_player.queue[guild_id]:
        return await ctx.send("Sırada şarkı yok.")
    
    # Sıra embed'i oluştur
    embed = discord.Embed(
        title="🎵 Şarkı Sırası",
        color=discord.Color.blue()
    )
    
    # Şu an çalan şarkı
    if guild_id in music_player.now_playing:
        now_playing = music_player.now_playing[guild_id]
        embed.add_field(
            name="Şu an oynatılıyor:",
            value=f"**{now_playing['title']}**",
            inline=False
        )
    
    # Sıradaki şarkılar
    queue_text = ""
    for i, song in enumerate(music_player.queue[guild_id]):
        queue_text += f"{i+1}. **{song['title']}**\n"
        
        # Çok uzunsa kısalt
        if i >= 9:  # İlk 10 şarkıyı göster
            remaining = len(music_player.queue[guild_id]) - 10
            queue_text += f"... ve {remaining} şarkı daha"
            break
            
    embed.add_field(name="Sıradaki şarkılar:", value=queue_text, inline=False)
    
    await ctx.send(embed=embed)

@bot.command(name='leave', help='Ses kanalından ayrılır')
async def leave(ctx):
    if ctx.voice_client:
        # Sırayı temizle
        guild_id = ctx.guild.id
        if guild_id in music_player.queue:
            music_player.queue[guild_id] = []
            
        # Şu an çalan şarkı bilgisini temizle
        music_player.now_playing.pop(guild_id, None)
        
        # Çalmayı durdur
        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            ctx.voice_client.stop()
        
        # Kanaldan ayrıl
        await ctx.voice_client.disconnect(force=True)
        await ctx.send("👋 Ses kanalından ayrıldım.")
    else:
        await ctx.send("Zaten bir ses kanalında değilim.")

@bot.command(name='ping', help='Bot gecikmesini gösterir')
async def ping(ctx):
    await ctx.send(f'🏓 Pong! {round(bot.latency * 1000)}ms')

@bot.command(name='np', help='Şu an çalan şarkıyı gösterir')
async def now_playing(ctx):
    guild_id = ctx.guild.id
    
    if guild_id not in music_player.now_playing:
        return await ctx.send("Şu anda çalan bir şarkı yok.")
    
    song_info = music_player.now_playing[guild_id]
    
    # Şarkı bilgilerini içeren bir embed oluştur
    embed = discord.Embed(
        title="🎵 Şu an Oynatılıyor",
        description=f"**{song_info['title']}**",
        color=discord.Color.blue()
    )
    
    if song_info.get('thumbnail'):
        embed.set_thumbnail(url=song_info['thumbnail'])
        
    embed.add_field(name="Yükleyen", value=song_info['uploader'], inline=True)
    
    if song_info.get('duration'):
        minutes, seconds = divmod(song_info['duration'], 60)
        embed.add_field(name="Süre", value=f"{minutes}:{seconds:02d}", inline=True)
        
    embed.add_field(name="Kaynak", value=f"[Link]({song_info['webpage_url']})", inline=True)
    
    await ctx.send(embed=embed)

# Slash komutları
@bot.tree.command(name="play", description="YouTube'dan müzik çalar (URL veya şarkı adı)")
async def slash_play(interaction: discord.Interaction, query: str):
    # Kullanıcı ses kanalında mı kontrol et
    if not interaction.user.voice:
        await interaction.response.send_message("Bir ses kanalında değilsiniz!", ephemeral=True)
        return
    
    # Etkileşimi ertele
    await interaction.response.defer(ephemeral=False)
    
    # Yükleniyor mesajı gönder
    loading_message = await interaction.followup.send("🔄 Şarkı yükleniyor, lütfen bekleyin...")
    
    try:
        # Önce şarkı bilgilerini al
        song_info = await music_player.show_search_results(interaction, query)
        
        # Yükleniyor mesajını sil
        try:
            await loading_message.delete()
        except:
            pass
        
        # Eğer doğrudan URL girildiyse ve song_info döndüyse
        if song_info:
            guild_id = interaction.guild.id
            
            # Bot zaten bağlı mı ve çalıyor mu kontrol et
            if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
                # Sıraya ekle
                if guild_id not in music_player.queue:
                    music_player.queue[guild_id] = []
                    
                music_player.queue[guild_id].append(song_info)
                
                # Sıraya eklendiğini bildir
                embed = discord.Embed(
                    title="🎵 Sıraya Eklendi",
                    description=f"**{song_info['title']}**",
                    color=discord.Color.green()
                )
                
                if song_info.get('thumbnail'):
                    embed.set_thumbnail(url=song_info['thumbnail'])
                    
                embed.add_field(name="Sıra Pozisyonu", value=f"#{len(music_player.queue[guild_id])}", inline=True)
                
                await interaction.followup.send(embed=embed)
            else:
                # Doğrudan çal (ses kanalına bağlanma işlemi play_song içinde yapılacak)
                await music_player.play_song(interaction, song_info)
            
    except Exception as e:
        # Yükleniyor mesajını sil
        try:
            await loading_message.delete()
        except:
            pass
        
        await interaction.followup.send(f"Bir hata oluştu: {str(e)}")
        print(f"Genel Hata: {str(e)}")

@bot.tree.command(name="pause", description="Müziği duraklatır")
async def slash_pause(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.pause()
        await interaction.followup.send("⏸️ Müzik duraklatıldı.")
    else:
        await interaction.followup.send("Şu anda çalan bir müzik yok.")

@bot.tree.command(name="resume", description="Duraklatılmış müziği devam ettirir")
async def slash_resume(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    
    if interaction.guild.voice_client and interaction.guild.voice_client.is_paused():
        interaction.guild.voice_client.resume()
        await interaction.followup.send("▶️ Müzik devam ediyor.")
    else:
        await interaction.followup.send("Duraklatılmış bir müzik yok.")

@bot.tree.command(name="stop", description="Müziği durdurur")
async def slash_stop(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    
    if interaction.guild.voice_client:
        if interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused():
            # Sırayı temizle
            guild_id = interaction.guild.id
            if guild_id in music_player.queue:
                music_player.queue[guild_id] = []
                
            # Şu an çalan şarkı bilgisini temizle
            music_player.now_playing.pop(guild_id, None)
            
            # Müziği durdur
            interaction.guild.voice_client.stop()
            await interaction.followup.send("⏹️ Müzik durduruldu ve sıra temizlendi!")
        else:
            await interaction.followup.send("Şu anda çalan bir müzik yok.")
    else:
        await interaction.followup.send("Bot bir ses kanalında değil.")

@bot.tree.command(name="skip", description="Sıradaki şarkıya geçer")
async def slash_skip(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    
    if interaction.guild.voice_client and (interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused()):
        interaction.guild.voice_client.stop()  # Şu anki şarkıyı durdur, play_next otomatik olarak çağrılacak
        await interaction.followup.send("⏭️ Şarkı geçildi.")
    else:
        await interaction.followup.send("Şu anda çalan bir müzik yok.")

@bot.tree.command(name="queue", description="Şarkı sırasını gösterir")
async def slash_queue(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    
    guild_id = interaction.guild.id
    
    if guild_id not in music_player.queue or not music_player.queue[guild_id]:
        return await interaction.followup.send("Sırada şarkı yok.")
    
    # Sıra embed'i oluştur
    embed = discord.Embed(
        title="🎵 Şarkı Sırası",
        color=discord.Color.blue()
    )
    
    # Şu an çalan şarkı
    if guild_id in music_player.now_playing:
        now_playing = music_player.now_playing[guild_id]
        embed.add_field(
            name="Şu an oynatılıyor:",
            value=f"**{now_playing['title']}**",
            inline=False
        )
    
    # Sıradaki şarkılar
    queue_text = ""
    for i, song in enumerate(music_player.queue[guild_id]):
        queue_text += f"{i+1}. **{song['title']}**\n"
        
        # Çok uzunsa kısalt
        if i >= 9:  # İlk 10 şarkıyı göster
            remaining = len(music_player.queue[guild_id]) - 10
            queue_text += f"... ve {remaining} şarkı daha"
            break
            
    embed.add_field(name="Sıradaki şarkılar:", value=queue_text, inline=False)
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="leave", description="Ses kanalından ayrılır")
async def slash_leave(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    
    if interaction.guild.voice_client:
        # Sırayı temizle
        guild_id = interaction.guild.id
        if guild_id in music_player.queue:
            music_player.queue[guild_id] = []
            
        # Şu an çalan şarkı bilgisini temizle
        music_player.now_playing.pop(guild_id, None)
        
        # Çalmayı durdur
        if interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused():
            interaction.guild.voice_client.stop()
        
        # Otomatik ayrılma görevini iptal et
        if guild_id in music_player.leave_tasks and not music_player.leave_tasks[guild_id].done():
            music_player.leave_tasks[guild_id].cancel()
        
        # Kanaldan ayrıl
        await interaction.guild.voice_client.disconnect(force=True)
        await interaction.followup.send("👋 Ses kanalından ayrıldım.")
    else:
        await interaction.followup.send("Zaten bir ses kanalında değilim.")

@bot.tree.command(name="np", description="Şu an çalan şarkıyı gösterir")
async def slash_now_playing(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    
    guild_id = interaction.guild.id
    
    if guild_id not in music_player.now_playing:
        return await interaction.followup.send("Şu anda çalan bir şarkı yok.")
    
    song_info = music_player.now_playing[guild_id]
    
    # Şarkı bilgilerini içeren bir embed oluştur
    embed = discord.Embed(
        title="🎵 Şu an Oynatılıyor",
        description=f"**{song_info['title']}**",
        color=discord.Color.blue()
    )
    
    if song_info.get('thumbnail'):
        embed.set_thumbnail(url=song_info['thumbnail'])
        
    embed.add_field(name="Yükleyen", value=song_info['uploader'], inline=True)
    
    if song_info.get('duration'):
        minutes, seconds = divmod(song_info['duration'], 60)
        embed.add_field(name="Süre", value=f"{minutes}:{seconds:02d}", inline=True)
        
    embed.add_field(name="Kaynak", value=f"[Link]({song_info['webpage_url']})", inline=True)
    
    await interaction.followup.send(embed=embed)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.send("Komut bulunamadı. Komutları görmek için `!help` yazın.")
    else:
        print(f"Hata: {str(error)}")
        await ctx.send(f"Bir hata oluştu: {str(error)}")

load_dotenv()

# Botu çalıştır
bot.run(os.getenv('DISCORD_TOKEN'))
